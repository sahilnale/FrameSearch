package main

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Store struct{ pool *pgxpool.Pool }

const videoColumns = "id::text,filename,status,duration_seconds,processing_error,created_at,object_key,content_type,size_bytes"

type scanner interface{ Scan(...any) error }

func scanVideo(row scanner) (Video, error) {
	var v Video
	e := row.Scan(&v.ID, &v.Filename, &v.Status, &v.Duration, &v.ProcessingError, &v.CreatedAt, &v.ObjectKey, &v.ContentType, &v.Size)
	if errors.Is(e, pgx.ErrNoRows) {
		return v, errNotFound
	}
	return v, e
}
func (s *Store) Create(ctx context.Context, v Video) error {
	_, e := s.pool.Exec(ctx, `INSERT INTO videos(id,filename,object_key,content_type,size_bytes,status) VALUES($1,$2,$3,$4,$5,'awaiting_upload')`, v.ID, v.Filename, v.ObjectKey, v.ContentType, v.Size)
	return e
}
func (s *Store) Get(ctx context.Context, id string) (Video, error) {
	return scanVideo(s.pool.QueryRow(ctx, "SELECT "+videoColumns+" FROM videos WHERE id=$1", id))
}
func (s *Store) List(ctx context.Context) ([]Video, error) {
	rows, e := s.pool.Query(ctx, "SELECT "+videoColumns+" FROM videos ORDER BY created_at DESC,id")
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []Video{}
	for rows.Next() {
		v, e := scanVideo(rows)
		if e != nil {
			return nil, e
		}
		out = append(out, v)
	}
	return out, rows.Err()
}
func (s *Store) Enqueue(ctx context.Context, id string, retry bool) (Video, *Job, error) {
	tx, e := s.pool.Begin(ctx)
	if e != nil {
		return Video{}, nil, e
	}
	defer tx.Rollback(ctx)
	v, e := scanVideo(tx.QueryRow(ctx, "SELECT "+videoColumns+" FROM videos WHERE id=$1 FOR UPDATE", id))
	if e != nil {
		return v, nil, e
	}
	create, e := enqueueDecision(v.Status, retry)
	if e != nil {
		return v, nil, e
	}
	if !create {
		return v, nil, nil
	}
	j := Job{uuid.NewString(), id}
	// Never silently override a live job when the video/job state is inconsistent.
	_, e = tx.Exec(ctx, `INSERT INTO processing_jobs(id,video_id,status) VALUES($1,$2,'queued')`, j.ID, j.VideoID)
	if e != nil {
		return v, nil, e
	}
	_, e = tx.Exec(ctx, `UPDATE videos SET status='queued',processing_error=NULL,updated_at=now() WHERE id=$1`, id)
	if e != nil {
		return v, nil, e
	}
	if e = tx.Commit(ctx); e != nil {
		return v, nil, e
	}
	v.Status = "queued"
	v.ProcessingError = nil
	return v, &j, nil
}
func vectorLiteral(v []float64) string {
	p := make([]string, len(v))
	for i, x := range v {
		p[i] = strconv.FormatFloat(x, 'g', -1, 64)
	}
	return "[" + strings.Join(p, ",") + "]"
}

const searchSQL = `SELECT v.id::text,f.id::text,v.filename,f.timestamp_ms,f.thumbnail_key,
 1-(f.embedding <=> $1::vector) AS similarity
 FROM video_frames f JOIN videos v ON v.id=f.video_id
 WHERE v.status='ready' AND f.model_version=$2 AND ($3::uuid IS NULL OR v.id=$3::uuid)
 ORDER BY f.embedding <=> $1::vector,f.id LIMIT $4`

func (s *Store) Search(ctx context.Context, vec []float64, r SearchRequest) ([]SearchResult, error) {
	rows, e := s.pool.Query(ctx, searchSQL, vectorLiteral(vec), modelVersion, r.VideoID, *r.Limit)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	out := []SearchResult{}
	for rows.Next() {
		var x SearchResult
		if e := rows.Scan(&x.VideoID, &x.FrameID, &x.Filename, &x.Timestamp, &x.ThumbnailKey, &x.Similarity); e != nil {
			return nil, e
		}
		out = append(out, x)
	}
	return out, rows.Err()
}
func (s *Store) Ping(ctx context.Context) error {
	var ok bool
	// A connection alone is insufficient: migrations and vector extension must exist.
	e := s.pool.QueryRow(ctx, `SELECT to_regclass('public.videos') IS NOT NULL AND to_regclass('public.video_frames') IS NOT NULL AND to_regclass('public.processing_jobs') IS NOT NULL AND EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')`).Scan(&ok)
	if e != nil {
		return e
	}
	if !ok {
		return errors.New("database migration not applied")
	}
	return nil
}
func (s *Store) Reconcile(ctx context.Context, p Publisher, stale bool, age time.Duration) (int, error) {
	if stale {
		tx, e := s.pool.Begin(ctx)
		if e != nil {
			return 0, e
		}
		defer tx.Rollback(ctx)
		// Caller must stop the sole processor before reclaiming; no lease is claimed.
		rows, e := tx.Query(ctx, `SELECT v.id::text,j.id::text FROM videos v JOIN processing_jobs j ON j.video_id=v.id WHERE j.status='processing' AND j.updated_at < now()-($1 * interval '1 second') ORDER BY v.id FOR UPDATE OF v,j`, age.Seconds())
		if e != nil {
			return 0, e
		}
		jobs := []Job{}
		for rows.Next() {
			var j Job
			if e = rows.Scan(&j.VideoID, &j.ID); e != nil {
				rows.Close()
				return 0, e
			}
			jobs = append(jobs, j)
		}
		rows.Close()
		if e = rows.Err(); e != nil {
			return 0, e
		}
		for _, j := range jobs {
			if _, e = tx.Exec(ctx, `UPDATE processing_jobs SET status='queued',updated_at=now(),last_error='requeued after processor crash' WHERE id=$1`, j.ID); e != nil {
				return 0, e
			}
			if _, e = tx.Exec(ctx, `UPDATE videos SET status='queued',processing_error=NULL,updated_at=now() WHERE id=$1`, j.VideoID); e != nil {
				return 0, e
			}
		}
		if e = tx.Commit(ctx); e != nil {
			return 0, e
		}
	}
	rows, e := s.pool.Query(ctx, `SELECT id::text,video_id::text FROM processing_jobs WHERE status='queued' ORDER BY updated_at,id`)
	if e != nil {
		return 0, e
	}
	jobs := []Job{}
	for rows.Next() {
		var j Job
		if e = rows.Scan(&j.ID, &j.VideoID); e != nil {
			rows.Close()
			return 0, e
		}
		jobs = append(jobs, j)
	}
	rows.Close()
	if e = rows.Err(); e != nil {
		return 0, e
	}
	n := 0
	for _, j := range jobs {
		if e = p.Publish(ctx, newEvent(j)); e != nil {
			return n, fmt.Errorf("publish queued job %s: %w", j.ID, e)
		}
		n++
	}
	return n, nil
}
