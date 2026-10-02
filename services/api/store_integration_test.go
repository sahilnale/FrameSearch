package main

import (
	"context"
	"errors"
	"math"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// TEST_DATABASE_URL must target a disposable PostgreSQL server with pgvector.
// Each test creates an isolated schema; it never deletes application rows.
func integrationStore(t *testing.T) *Store {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset; real PostgreSQL/pgvector integration not run")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	admin, e := pgxpool.New(ctx, dsn)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = admin.Exec(ctx, "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"); e != nil {
		admin.Close()
		t.Fatal(e)
	}
	schema := "api_test_" + uuid.New().String()[:8]
	if _, e = admin.Exec(ctx, "CREATE SCHEMA "+pgx.Identifier{schema}.Sanitize()); e != nil {
		admin.Close()
		t.Fatal(e)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if _, e := admin.Exec(ctx, "DROP SCHEMA "+pgx.Identifier{schema}.Sanitize()+" CASCADE"); e != nil {
			t.Errorf("cleanup schema: %v", e)
		}
		admin.Close()
	})
	cfg, e := pgxpool.ParseConfig(dsn)
	if e != nil {
		t.Fatal(e)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema + ",public"
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(pool.Close)
	migration, e := os.ReadFile(filepath.Join("..", "..", "db", "migrations", "001_initial.sql"))
	if e != nil {
		t.Fatal(e)
	}
	if _, e = pool.Exec(ctx, string(migration)); e != nil {
		t.Fatal(e)
	}
	// The initial migration must also be safe to rerun.
	if _, e = pool.Exec(ctx, string(migration)); e != nil {
		t.Fatalf("migration rerun: %v", e)
	}
	return &Store{pool}
}
func createDBVideo(t *testing.T, s *Store) Video {
	t.Helper()
	id := uuid.NewString()
	v := Video{ID: id, Filename: "clip.mp4", ObjectKey: "videos/" + id + "/original.mp4", ContentType: "video/mp4", Size: 123}
	if e := s.Create(context.Background(), v); e != nil {
		t.Fatal(e)
	}
	return v
}
func TestDBConcurrentEnqueueAndRetry(t *testing.T) {
	s := integrationStore(t)
	v := createDBVideo(t, s)
	ctx := context.Background()
	var wg sync.WaitGroup
	var mu sync.Mutex
	created := 0
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			saved, j, e := s.Enqueue(ctx, v.ID, false)
			if e != nil {
				t.Error(e)
				return
			}
			if saved.Status != "queued" {
				t.Errorf("wrong state: %s", saved.Status)
			}
			if j != nil {
				mu.Lock()
				created++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if created != 1 {
		t.Fatalf("created %d jobs", created)
	}
	var count int
	if e := s.pool.QueryRow(ctx, "SELECT count(*) FROM processing_jobs WHERE video_id=$1", v.ID).Scan(&count); e != nil {
		t.Fatal(e)
	}
	if count != 1 {
		t.Fatalf("persisted %d jobs", count)
	}
	if _, _, e := s.Enqueue(ctx, v.ID, true); !errors.Is(e, errConflict) {
		t.Fatalf("retry queued video should conflict: %v", e)
	}
	// Emulate the processor's durable terminal-failure transaction, not media inference.
	tx, e := s.pool.Begin(ctx)
	if e != nil {
		t.Fatal(e)
	}
	defer tx.Rollback(ctx)
	if _, e = tx.Exec(ctx, "UPDATE videos SET status='failed',processing_error='invalid video' WHERE id=$1", v.ID); e != nil {
		t.Fatal(e)
	}
	if _, e = tx.Exec(ctx, "UPDATE processing_jobs SET status='failed',last_error='invalid video' WHERE video_id=$1", v.ID); e != nil {
		t.Fatal(e)
	}
	if e = tx.Commit(ctx); e != nil {
		t.Fatal(e)
	}
	saved, job, e := s.Enqueue(ctx, v.ID, true)
	if e != nil || job == nil || saved.Status != "queued" || saved.ProcessingError != nil {
		t.Fatalf("retry: video=%+v job=%+v err=%v", saved, job, e)
	}
	if _, e = s.pool.Exec(ctx, "INSERT INTO processing_jobs(id,video_id,status) VALUES($1,$2,'queued')", uuid.NewString(), v.ID); e == nil {
		t.Fatal("partial unique index allowed another active job")
	}
}
func TestDBCosineSearchFiltersAndOrdering(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	ready := createDBVideo(t, s)
	other := createDBVideo(t, s)
	partial := createDBVideo(t, s)
	for _, v := range []Video{ready, other} {
		if _, e := s.pool.Exec(ctx, "UPDATE videos SET status='ready' WHERE id=$1", v.ID); e != nil {
			t.Fatal(e)
		}
	}
	insert := func(id string, stamp int, vec []float64, version string) string {
		t.Helper()
		fid := uuid.NewString()
		if _, e := s.pool.Exec(ctx, `INSERT INTO video_frames(id,video_id,timestamp_ms,thumbnail_key,embedding,model_version) VALUES($1,$2,$3,$4,$5::vector,$6)`, fid, id, stamp, "frames/"+fid+".jpg", vectorLiteral(vec), version); e != nil {
			t.Fatal(e)
		}
		return fid
	}
	best := insert(ready.ID, 0, unitVector(), modelVersion)
	mixed := make([]float64, 512)
	mixed[0] = 0.6
	mixed[1] = 0.8
	second := insert(ready.ID, 3000, mixed, modelVersion)
	opposite := unitVector()
	opposite[0] = -1
	insert(other.ID, 0, opposite, modelVersion)
	insert(partial.ID, 0, unitVector(), modelVersion)
	insert(ready.ID, 6000, unitVector(), "old-model")
	req := SearchRequest{Query: "car", Limit: ptr(30)}
	results, e := s.Search(ctx, unitVector(), req)
	if e != nil {
		t.Fatal(e)
	}
	if len(results) != 3 || results[0].FrameID != best || results[1].FrameID != second {
		t.Fatalf("filters or ordering failed: %+v", results)
	}
	for i, want := range []float64{1, 0.6, -1} {
		if math.Abs(results[i].Similarity-want) > 1e-5 {
			t.Fatalf("cosine similarity %d=%f expected %f", i, results[i].Similarity, want)
		}
	}
	req.VideoID = &ready.ID
	results, e = s.Search(ctx, unitVector(), req)
	if e != nil || len(results) != 2 {
		t.Fatalf("video filter: %+v %v", results, e)
	}
	req.Limit = ptr(1)
	results, e = s.Search(ctx, unitVector(), req)
	if e != nil || len(results) != 1 || results[0].Timestamp != 0 {
		t.Fatalf("limit or timestamp: %+v %v", results, e)
	}
}
func TestDBReconcileQueuedAndStale(t *testing.T) {
	s := integrationStore(t)
	ctx := context.Background()
	v := createDBVideo(t, s)
	_, job, e := s.Enqueue(ctx, v.ID, false)
	if e != nil {
		t.Fatal(e)
	}
	pub := &testPublisher{}
	n, e := s.Reconcile(ctx, pub, false, 15*time.Minute)
	if e != nil || n != 1 || pub.events[0].JobID != job.ID {
		t.Fatalf("queued reconciliation: %d %v", n, e)
	}
	if _, e = s.pool.Exec(ctx, "UPDATE videos SET status='processing' WHERE id=$1", v.ID); e != nil {
		t.Fatal(e)
	}
	if _, e = s.pool.Exec(ctx, "UPDATE processing_jobs SET status='processing',updated_at=now()-interval '1 hour' WHERE id=$1", job.ID); e != nil {
		t.Fatal(e)
	}
	n, e = s.Reconcile(ctx, pub, false, 15*time.Minute)
	if e != nil || n != 0 {
		t.Fatalf("normal reconciliation reclaimed a live job: %d %v", n, e)
	}
	n, e = s.Reconcile(ctx, pub, true, 15*time.Minute)
	if e != nil || n != 1 {
		t.Fatalf("stale recovery: %d %v", n, e)
	}
	saved, e := s.Get(ctx, v.ID)
	if e != nil || saved.Status != "queued" {
		t.Fatalf("recovered status: %+v %v", saved, e)
	}
	var count int
	if e = s.pool.QueryRow(ctx, "SELECT count(*) FROM processing_jobs WHERE video_id=$1", v.ID).Scan(&count); e != nil || count != 1 {
		t.Fatalf("recovery duplicated job: %d %v", count, e)
	}
}
