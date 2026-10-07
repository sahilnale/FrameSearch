package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

func TestSignedURLsUseBrowserEndpoint(t *testing.T) {
	s := newStorage("http://minio:9000", "http://localhost:9000", "test-key", "test-secret", "framesearch")
	for _, tc := range []struct {
		name string
		sign func(context.Context, string) (string, error)
	}{{"PUT", s.PutURL}, {"GET", s.GetURL}} {
		t.Run(tc.name, func(t *testing.T) {
			raw, e := tc.sign(context.Background(), "videos/test/original.mp4")
			if e != nil {
				t.Fatal(e)
			}
			u, e := url.Parse(raw)
			if e != nil {
				t.Fatal(e)
			}
			if u.Host != "localhost:9000" || u.Path != "/framesearch/videos/test/original.mp4" || u.Query().Get("X-Amz-Expires") != "900" || u.Query().Get("X-Amz-Signature") == "" {
				t.Fatalf("wrong presigned URL endpoint, key or expiry: %s", raw)
			}
			if strings.Contains(raw, "test-secret") {
				t.Fatal("signer exposed secret")
			}
		})
	}
}
func TestS3HeadHTTP(t *testing.T) {
	for _, status := range []int{200, 404, 403} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "HEAD" || r.URL.Path != "/framesearch/videos/test/original.mp4" {
					t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
				}
				w.Header().Set("Content-Length", "123")
				w.Header().Set("Content-Type", "video/mp4")
				w.WriteHeader(status)
			}))
			defer srv.Close()
			s := newStorage(srv.URL, "http://localhost:9000", "key", "secret", "framesearch")
			info, e := s.Head(context.Background(), "videos/test/original.mp4")
			switch status {
			case 200:
				if e != nil || info.Size != 123 || info.ContentType != "video/mp4" {
					t.Fatalf("head=%+v err=%v", info, e)
				}
			case 404:
				if e != errObjectMissing {
					t.Fatalf("404 not recognized: %v", e)
				}
			case 403:
				if e == nil || e == errObjectMissing {
					t.Fatalf("403 must remain a dependency error: %v", e)
				}
			}
		})
	}
}
func TestProcessorClientHTTP(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   string
		valid  bool
	}{
		{"valid response", 200, func() string {
			b, _ := json.Marshal(map[string]any{"embedding": unitVector(), "model_version": modelVersion})
			return string(b)
		}(), true},
		{"malformed response", 200, `{`, false},
		{"trailing response", 200, `{"embedding":[]} {}`, false},
		{"non-200 response", 503, `{"error":"loading model"}`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "POST" || r.URL.Path != "/embed/text" || r.Header.Get("Content-Type") != "application/json" {
					t.Errorf("unexpected processor request: %s %s", r.Method, r.URL.Path)
				}
				var x map[string]string
				if e := json.NewDecoder(r.Body).Decode(&x); e != nil || len(x) != 1 || x["text"] != "a car" {
					t.Errorf("wrong processor payload: %+v error=%v", x, e)
				}
				w.WriteHeader(tc.status)
				w.Write([]byte(tc.body))
			}))
			defer srv.Close()
			p := &ProcessorClient{srv.URL, srv.Client()}
			vec, version, e := p.Embed(context.Background(), "a car")
			if (e == nil) != tc.valid {
				t.Fatalf("valid=%v error=%v", tc.valid, e)
			}
			if tc.valid {
				if e := validateEmbedding(vec, version); e != nil {
					t.Fatal(e)
				}
			}
		})
	}
}
func TestProcessorDeadline(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.Copy(io.Discard, r.Body); <-r.Context().Done() }))
	defer srv.Close()
	p := &ProcessorClient{srv.URL, srv.Client()}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if _, _, e := p.Embed(ctx, "a car"); e == nil {
		t.Fatal("processor request ignored deadline")
	}
}
func TestProcessorReadiness(t *testing.T) {
	for _, status := range []int{200, 503} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path != "/health/ready" || r.Method != "GET" {
				t.Errorf("wrong readiness request")
			}
			w.WriteHeader(status)
		}))
		p := &ProcessorClient{srv.URL, &http.Client{Timeout: time.Second}}
		e := p.Ping(context.Background())
		srv.Close()
		if (e == nil) != (status == 200) {
			t.Fatalf("status=%d error=%v", status, e)
		}
	}
}
