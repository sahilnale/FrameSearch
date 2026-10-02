package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"time"
)

type API struct {
	repo      Repository
	storage   Storage
	publisher Publisher
	processor Embedder
	origin    string
}

func jsonResponse(w http.ResponseWriter, status int, x any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if e := json.NewEncoder(w).Encode(x); e != nil {
		slog.Warn("response write failed", "error", e)
	}
}
func apiError(w http.ResponseWriter, status int, code, message string) {
	jsonResponse(w, status, map[string]any{"error": map[string]string{"code": code, "message": message}})
}
func internalError(w http.ResponseWriter, e error) {
	switch {
	case errors.Is(e, errNotFound):
		apiError(w, 404, "not_found", "video not found")
	case errors.Is(e, errConflict):
		apiError(w, 409, "invalid_state", "video state does not allow this operation")
	default:
		slog.Error("dependency request failed", "error", e)
		apiError(w, 503, "dependency_unavailable", "a required service is unavailable; please retry")
	}
}
func decode(w http.ResponseWriter, r *http.Request, x any) bool {
	if ct := strings.ToLower(strings.TrimSpace(strings.Split(r.Header.Get("Content-Type"), ";")[0])); ct != "application/json" {
		apiError(w, 415, "unsupported_media_type", "Content-Type must be application/json")
		return false
	}
	body, e := io.ReadAll(http.MaxBytesReader(w, r.Body, 16*1024))
	if e != nil {
		apiError(w, 400, "invalid_json", "request body exceeds 16384 bytes or could not be read")
		return false
	}
	// Reject null, arrays, duplicate fields and trailing JSON rather than accepting
	// ambiguous inputs. Typed decoding below also rejects unknown fields.
	d := json.NewDecoder(bytes.NewReader(body))
	token, e := d.Token()
	if e != nil || token != json.Delim('{') {
		apiError(w, 400, "invalid_json", "request must contain one JSON object")
		return false
	}
	seen := map[string]bool{}
	for d.More() {
		token, e = d.Token()
		if e != nil {
			apiError(w, 400, "invalid_json", "invalid JSON object")
			return false
		}
		key, ok := token.(string)
		if !ok || seen[key] {
			apiError(w, 400, "invalid_json", "duplicate or invalid JSON field")
			return false
		}
		seen[key] = true
		var raw json.RawMessage
		if e = d.Decode(&raw); e != nil {
			apiError(w, 400, "invalid_json", "invalid JSON field value")
			return false
		}
	}
	if token, e = d.Token(); e != nil || token != json.Delim('}') {
		apiError(w, 400, "invalid_json", "invalid JSON object")
		return false
	}
	var extra any
	if e = d.Decode(&extra); e != io.EOF {
		apiError(w, 400, "invalid_json", "request must contain exactly one JSON object")
		return false
	}
	d = json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if e = d.Decode(x); e != nil {
		apiError(w, 400, "invalid_json", "request fields must have the expected names and types")
		return false
	}
	return true
}
func pathID(w http.ResponseWriter, r *http.Request) (string, bool) {
	id, e := uuid.Parse(chi.URLParam(r, "id"))
	if e != nil {
		apiError(w, 400, "invalid_id", "video id must be a UUID")
		return "", false
	}
	return id.String(), true
}
func (a *API) Handler() http.Handler {
	r := chi.NewRouter()
	r.Use(func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
			defer cancel()
			defer func() {
				if e := recover(); e != nil {
					slog.Error("request panic", "panic", e)
					apiError(w, 500, "internal_error", "unexpected server error")
				}
				slog.Info("request", "method", r.Method, "path", r.URL.Path, "duration_ms", time.Since(start).Milliseconds())
			}()
			if origin := r.Header.Get("Origin"); origin != "" {
				w.Header().Add("Vary", "Origin")
				if origin != a.origin {
					apiError(w, 403, "origin_not_allowed", "origin is not permitted")
					return
				}
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
				w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
				w.Header().Set("Access-Control-Max-Age", "600")
			}
			if r.Method == http.MethodOptions {
				w.WriteHeader(204)
				return
			}
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	})
	r.NotFound(func(w http.ResponseWriter, r *http.Request) { apiError(w, 404, "not_found", "endpoint not found") })
	r.MethodNotAllowed(func(w http.ResponseWriter, r *http.Request) {
		apiError(w, 405, "method_not_allowed", "method not allowed")
	})
	r.Get("/health/live", func(w http.ResponseWriter, r *http.Request) {
		jsonResponse(w, 200, map[string]string{"status": "live"})
	})
	r.Get("/health/ready", a.ready)
	r.Route("/api/v1", func(r chi.Router) {
		r.Post("/videos/upload-url", a.upload)
		r.Get("/videos", a.list)
		r.Get("/videos/{id}", a.detail)
		r.Post("/videos/{id}/complete", a.complete)
		r.Post("/videos/{id}/retry", a.retry)
		r.Get("/videos/{id}/playback-url", a.playback)
	})
	return r
}
func (a *API) ready(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	checks := []struct {
		name string
		ping func(context.Context) error
	}{{"database", a.repo.Ping}, {"storage", a.storage.Ping}, {"kafka", a.publisher.Ping}, {"processor", a.processor.Ping}}
	type result struct {
		name string
		err  error
	}
	ch := make(chan result, len(checks))
	for _, check := range checks {
		go func(name string, ping func(context.Context) error) { ch <- result{name, ping(ctx)} }(check.name, check.ping)
	}
	deps := map[string]string{}
	status := 200
	for range checks {
		select {
		case x := <-ch:
			if x.err != nil {
				deps[x.name] = "unavailable"
				status = 503
				slog.Warn("readiness failed", "dependency", x.name, "error", x.err)
			} else {
				deps[x.name] = "ready"
			}
		case <-ctx.Done():
			apiError(w, 503, "not_ready", "dependency readiness check timed out")
			return
		}
	}
	if status != 200 {
		apiError(w, 503, "not_ready", "one or more dependencies are unavailable")
		return
	}
	jsonResponse(w, status, map[string]any{"status": "ready", "dependencies": deps})
}
func (a *API) upload(w http.ResponseWriter, r *http.Request) {
	var x UploadRequest
	if !decode(w, r, &x) {
		return
	}
	if e := x.validate(); e != nil {
		apiError(w, 400, "invalid_upload", e.Error())
		return
	}
	id := uuid.NewString()
	key := "videos/" + id + "/original.mp4"
	url, e := a.storage.PutURL(r.Context(), key)
	if e != nil {
		internalError(w, e)
		return
	}
	v := Video{ID: id, Filename: x.Filename, ObjectKey: key, ContentType: x.ContentType, Size: x.Size}
	if e = a.repo.Create(r.Context(), v); e != nil {
		internalError(w, e)
		return
	}
	jsonResponse(w, 201, map[string]string{"video_id": id, "upload_url": url, "object_key": key})
}
func (a *API) list(w http.ResponseWriter, r *http.Request) {
	vs, e := a.repo.List(r.Context())
	if e != nil {
		internalError(w, e)
		return
	}
	if vs == nil {
		vs = []Video{}
	}
	jsonResponse(w, 200, map[string]any{"videos": vs})
}
func (a *API) detail(w http.ResponseWriter, r *http.Request) {
	id, ok := pathID(w, r)
	if !ok {
		return
	}
	v, e := a.repo.Get(r.Context(), id)
	if e != nil {
		internalError(w, e)
		return
	}
	jsonResponse(w, 200, v)
}
func (a *API) complete(w http.ResponseWriter, r *http.Request) { a.enqueue(w, r, false) }
func (a *API) retry(w http.ResponseWriter, r *http.Request)    { a.enqueue(w, r, true) }
func (a *API) enqueue(w http.ResponseWriter, r *http.Request, retry bool) {
	id, ok := pathID(w, r)
	if !ok {
		return
	}
	v, e := a.repo.Get(r.Context(), id)
	if e != nil {
		internalError(w, e)
		return
	}
	create, e := enqueueDecision(v.Status, retry)
	if e != nil {
		internalError(w, e)
		return
	}
	if create {
		info, e := a.storage.Head(r.Context(), v.ObjectKey)
		if e != nil {
			if errors.Is(e, errObjectMissing) {
				apiError(w, 409, "upload_missing", "upload the video object before completing")
			} else {
				internalError(w, e)
			}
			return
		}
		if info.Size != v.Size || info.ContentType != v.ContentType {
			apiError(w, 409, "upload_mismatch", "uploaded object size or Content-Type differs from upload declaration")
			return
		}
	}
	v, job, e := a.repo.Enqueue(r.Context(), id, retry)
	if e != nil {
		internalError(w, e)
		return
	}
	if job != nil {
		if e = a.publisher.Publish(r.Context(), newEvent(*job)); e != nil {
			slog.Error("Kafka publish failed; job remains queued", "job_id", job.ID, "error", e)
			apiError(w, 503, "queued_publish_failed", "job was saved but publication failed; run make reconcile")
			return
		}
	}
	jsonResponse(w, 200, map[string]string{"video_id": id, "status": v.Status})
}
func (a *API) playback(w http.ResponseWriter, r *http.Request) {
	id, ok := pathID(w, r)
	if !ok {
		return
	}
	v, e := a.repo.Get(r.Context(), id)
	if e != nil {
		internalError(w, e)
		return
	}
	if v.Status != "ready" {
		internalError(w, errConflict)
		return
	}
	url, e := a.storage.GetURL(r.Context(), v.ObjectKey)
	if e != nil {
		internalError(w, e)
		return
	}
	jsonResponse(w, 200, map[string]any{"url": url, "expires_in_seconds": 900})
}
