package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
)

// Test doubles isolate HTTP behavior; production still uses pgvector, S3, Kafka and the real processor.
type memoryRepository struct {
	mu        sync.Mutex
	video     Video
	jobs      int
	results   []SearchResult
	err       error
	readiness error
}

func (m *memoryRepository) Create(ctx context.Context, v Video) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	v.Status = "awaiting_upload"
	v.CreatedAt = time.Now().UTC()
	m.video = v
	return m.err
}
func (m *memoryRepository) Get(ctx context.Context, id string) (Video, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.err != nil {
		return Video{}, m.err
	}
	if m.video.ID != id {
		return Video{}, errNotFound
	}
	return m.video, nil
}
func (m *memoryRepository) List(ctx context.Context) ([]Video, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.video.ID == "" {
		return nil, m.err
	}
	return []Video{m.video}, m.err
}
func (m *memoryRepository) Enqueue(ctx context.Context, id string, retry bool) (Video, *Job, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.err != nil {
		return Video{}, nil, m.err
	}
	if m.video.ID != id {
		return Video{}, nil, errNotFound
	}
	create, e := enqueueDecision(m.video.Status, retry)
	if e != nil {
		return m.video, nil, e
	}
	if !create {
		return m.video, nil, nil
	}
	m.jobs++
	m.video.Status = "queued"
	m.video.ProcessingError = nil
	return m.video, &Job{uuid.NewString(), id}, nil
}
func (m *memoryRepository) Search(ctx context.Context, v []float64, r SearchRequest) ([]SearchResult, error) {
	return append([]SearchResult(nil), m.results...), m.err
}
func (m *memoryRepository) Ping(ctx context.Context) error { return m.readiness }

type testStorage struct {
	info      ObjectInfo
	err       error
	readiness error
}

func (s *testStorage) PutURL(ctx context.Context, key string) (string, error) {
	return "http://localhost:9000/framesearch/" + key + "?signed=put", s.err
}
func (s *testStorage) GetURL(ctx context.Context, key string) (string, error) {
	return "http://localhost:9000/framesearch/" + key + "?signed=get", s.err
}
func (s *testStorage) Head(ctx context.Context, key string) (ObjectInfo, error) { return s.info, s.err }
func (s *testStorage) Ping(ctx context.Context) error                           { return s.readiness }

type testPublisher struct {
	mu        sync.Mutex
	events    []Event
	err       error
	readiness error
}

func (p *testPublisher) Publish(ctx context.Context, e Event) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.events = append(p.events, e)
	return p.err
}
func (p *testPublisher) Ping(ctx context.Context) error { return p.readiness }

type testEmbedder struct {
	vec       []float64
	version   string
	err       error
	readiness error
}

func (p *testEmbedder) Embed(ctx context.Context, text string) ([]float64, string, error) {
	return p.vec, p.version, p.err
}
func (p *testEmbedder) Ping(ctx context.Context) error { return p.readiness }
func fixture() (*API, *memoryRepository, *testStorage, *testPublisher, *testEmbedder) {
	repo := &memoryRepository{video: Video{ID: uuid.NewString(), Filename: "clip.mp4", Status: "awaiting_upload", Size: 123, ContentType: "video/mp4", ObjectKey: "videos/test/original.mp4", CreatedAt: time.Now().UTC()}}
	storage := &testStorage{info: ObjectInfo{123, "video/mp4"}}
	publisher := &testPublisher{}
	embedder := &testEmbedder{vec: unitVector(), version: modelVersion}
	return &API{repo, storage, publisher, embedder, "http://localhost:3000"}, repo, storage, publisher, embedder
}
func request(h http.Handler, method, path, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, req)
	return w
}
func decodeObject(t *testing.T, w *httptest.ResponseRecorder) map[string]json.RawMessage {
	t.Helper()
	if w.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("wrong Content-Type: %s", w.Header().Get("Content-Type"))
	}
	var out map[string]json.RawMessage
	if e := json.Unmarshal(w.Body.Bytes(), &out); e != nil {
		t.Fatalf("invalid JSON: %v (%s)", e, w.Body.String())
	}
	return out
}
func assertError(t *testing.T, w *httptest.ResponseRecorder, status int, code string) {
	t.Helper()
	if w.Code != status {
		t.Fatalf("status=%d want=%d body=%s", w.Code, status, w.Body.String())
	}
	out := decodeObject(t, w)
	if len(out) != 1 {
		t.Fatalf("unexpected error envelope: %s", w.Body.String())
	}
	var x struct{ Code, Message string }
	if e := json.Unmarshal(out["error"], &x); e != nil {
		t.Fatal(e)
	}
	if x.Code != code || x.Message == "" {
		t.Fatalf("unexpected error: %+v", x)
	}
}
func TestUploadHTTP(t *testing.T) {
	a, repo, _, _, _ := fixture()
	w := request(a.Handler(), "POST", "/api/v1/videos/upload-url", `{"filename":"clip.mp4","content_type":"video/mp4","size_bytes":123}`)
	if w.Code != 201 {
		t.Fatalf("status=%d body=%s", w.Code, w.Body.String())
	}
	out := decodeObject(t, w)
	if len(out) != 3 {
		t.Fatalf("unexpected fields: %s", w.Body.String())
	}
	var id, key, url string
	json.Unmarshal(out["video_id"], &id)
	json.Unmarshal(out["object_key"], &key)
	json.Unmarshal(out["upload_url"], &url)
	if _, e := uuid.Parse(id); e != nil {
		t.Fatal(e)
	}
	if key != "videos/"+id+"/original.mp4" || !strings.HasPrefix(url, "http://localhost:9000/") {
		t.Fatalf("wrong keys or browser URL: %s", w.Body.String())
	}
	if repo.video.Status != "awaiting_upload" || repo.video.Size != 123 {
		t.Fatalf("wrong saved state: %+v", repo.video)
	}
}

func TestCompleteIdempotency(t *testing.T) {
	a, repo, storage, pub, _ := fixture()
	path := "/api/v1/videos/" + repo.video.ID + "/complete"
	h := a.Handler()
	for i := 0; i < 2; i++ {
		w := request(h, "POST", path, "")
		if w.Code != 200 {
			t.Fatalf("complete %d: %s", i, w.Body.String())
		}
		out := decodeObject(t, w)
		if len(out) != 2 || string(out["status"]) != `"queued"` {
			t.Fatalf("wrong contract: %s", w.Body.String())
		}
	}
	if repo.jobs != 1 || len(pub.events) != 1 {
		t.Fatalf("duplicate work: jobs=%d events=%d", repo.jobs, len(pub.events))
	}
	for _, status := range []string{"processing", "ready"} {
		repo.video.Status = status
		storage.err = errors.New("storage down")
		w := request(h, "POST", path, "")
		if w.Code != 200 {
			t.Fatalf("repeat %s should not need storage: %s", status, w.Body.String())
		}
		out := decodeObject(t, w)
		if string(out["status"]) != `"`+status+`"` {
			t.Fatal(w.Body.String())
		}
	}
	if repo.jobs != 1 || len(pub.events) != 1 {
		t.Fatal("processing/ready repeat created work")
	}
}
func TestConcurrentCompleteHTTP(t *testing.T) {
	a, repo, _, pub, _ := fixture()
	path := "/api/v1/videos/" + repo.video.ID + "/complete"
	h := a.Handler()
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			w := request(h, "POST", path, "")
			if w.Code != 200 {
				t.Errorf("status=%d body=%s", w.Code, w.Body.String())
			}
		}()
	}
	wg.Wait()
	if repo.jobs != 1 || len(pub.events) != 1 {
		t.Fatalf("duplicate work: jobs=%d events=%d", repo.jobs, len(pub.events))
	}
}
func TestCompleteObjectValidation(t *testing.T) {
	for _, tc := range []struct {
		name   string
		info   ObjectInfo
		err    error
		status int
		code   string
	}{
		{"missing", ObjectInfo{}, errObjectMissing, 409, "upload_missing"},
		{"size mismatch", ObjectInfo{124, "video/mp4"}, nil, 409, "upload_mismatch"},
		{"content mismatch", ObjectInfo{123, "image/png"}, nil, 409, "upload_mismatch"},
		{"storage unavailable", ObjectInfo{}, errors.New("down"), 503, "dependency_unavailable"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			a, repo, s, pub, _ := fixture()
			s.info = tc.info
			s.err = tc.err
			assertError(t, request(a.Handler(), "POST", "/api/v1/videos/"+repo.video.ID+"/complete", ""), tc.status, tc.code)
			if repo.jobs != 0 || len(pub.events) != 0 || repo.video.Status != "awaiting_upload" {
				t.Fatal("invalid object created work")
			}
		})
	}
}
func TestPublishGap(t *testing.T) {
	a, repo, _, pub, _ := fixture()
	pub.err = errors.New("Kafka unavailable")
	path := "/api/v1/videos/" + repo.video.ID + "/complete"
	h := a.Handler()
	assertError(t, request(h, "POST", path, ""), 503, "queued_publish_failed")
	if repo.video.Status != "queued" || repo.jobs != 1 {
		t.Fatal("durable queued state lost")
	}
	w := request(h, "POST", path, "")
	if w.Code != 200 || repo.jobs != 1 || len(pub.events) != 1 {
		t.Fatalf("repeat complete should preserve queue and not republish: %s", w.Body.String())
	}
	e := pub.events[0]
	if e.EventType != "media.uploaded" || e.SchemaVersion != 1 || e.VideoID != repo.video.ID || e.JobID == "" || e.CreatedAt.IsZero() {
		t.Fatalf("wrong envelope: %+v", e)
	}
	if _, err := uuid.Parse(e.EventID); err != nil {
		t.Fatal(err)
	}
	b, _ := json.Marshal(e)
	var out map[string]any
	json.Unmarshal(b, &out)
	if len(out) != 6 {
		t.Fatalf("wrong event fields: %s", b)
	}
}

func TestVideoJSONAndPlayback(t *testing.T) {
	a, repo, _, _, _ := fixture()
	h := a.Handler()
	base := "/api/v1/videos/" + repo.video.ID
	w := request(h, "GET", base, "")
	if w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	out := decodeObject(t, w)
	if len(out) != 6 || string(out["duration_seconds"]) != "null" || string(out["processing_error"]) != "null" {
		t.Fatalf("wrong video fields: %s", w.Body.String())
	}
	for _, field := range []string{"id", "filename", "status", "duration_seconds", "processing_error", "created_at"} {
		if _, ok := out[field]; !ok {
			t.Fatalf("missing %s", field)
		}
	}
	repo.video = Video{}
	w = request(h, "GET", "/api/v1/videos", "")
	out = decodeObject(t, w)
	if string(out["videos"]) != "[]" {
		t.Fatal("empty list must be array")
	}
	assertError(t, request(h, "GET", base, ""), 404, "not_found")
}

func TestHealthCORSAndErrors(t *testing.T) {
	a, repo, _, _, _ := fixture()
	h := a.Handler()
	if w := request(h, "GET", "/health/live", ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if w := request(h, "GET", "/health/ready", ""); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	repo.readiness = errors.New("database unavailable")
	assertError(t, request(h, "GET", "/health/ready", ""), 503, "not_ready")
	assertError(t, request(h, "GET", "/no-such-route", ""), 404, "not_found")
	assertError(t, request(h, "DELETE", "/api/v1/videos", ""), 405, "method_not_allowed")
	for _, origin := range []string{"http://localhost:3000", "http://elsewhere.invalid"} {
		req := httptest.NewRequest("OPTIONS", "/api/v1/search", nil)
		req.Header.Set("Origin", origin)
		w := httptest.NewRecorder()
		h.ServeHTTP(w, req)
		if origin == a.origin {
			if w.Code != 204 || w.Header().Get("Access-Control-Allow-Origin") != origin {
				t.Fatal("CORS preflight failed")
			}
		} else {
			assertError(t, w, 403, "origin_not_allowed")
		}
	}
}
