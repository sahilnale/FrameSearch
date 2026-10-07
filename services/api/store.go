package main

import (
	"context"
	"errors"

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
