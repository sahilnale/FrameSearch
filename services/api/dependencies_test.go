package main

import (
	"context"

	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
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
