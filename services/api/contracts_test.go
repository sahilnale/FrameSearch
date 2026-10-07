package main

import (
	"math"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestUploadValidation(t *testing.T) {
	for _, tc := range []struct {
		name, filename, contentType string
		size                        int64
		valid                       bool
	}{
		{"minimum", "clip.mp4", "video/mp4", 1, true},
		{"maximum", "clip.mp4", "video/mp4", maxUploadBytes, true},
		{"unicode filename", "街景.mp4", "video/mp4", 123, true},
		{"media proof deferred to processor", "not-an-extension", "video/mp4", 123, true},
		{"zero size", "clip.mp4", "video/mp4", 0, false},
		{"negative size", "clip.mp4", "video/mp4", -1, false},
		{"oversize", "clip.mp4", "video/mp4", maxUploadBytes + 1, false},
		{"wrong type", "clip.mp4", "image/png", 123, false},
		{"empty filename", "", "video/mp4", 123, false},
		{"blank filename", "  ", "video/mp4", 123, false},
		{"parent path", "../clip.mp4", "video/mp4", 123, false},
		{"windows path", `C:\clip.mp4`, "video/mp4", 123, false},
		{"control character", "clip\n.mp4", "video/mp4", 123, false},
		{"long filename", strings.Repeat("a", 256), "video/mp4", 123, false},
		{"dot", ".", "video/mp4", 123, false},
		{"parent dot", "..", "video/mp4", 123, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := (UploadRequest{tc.filename, tc.contentType, tc.size}).validate()
			if (e == nil) != tc.valid {
				t.Fatalf("valid=%v error=%v", tc.valid, e)
			}
		})
	}
}
func ptr[T any](x T) *T { return &x }
func TestSearchValidation(t *testing.T) {
	for _, tc := range []struct {
		name  string
		r     SearchRequest
		valid bool
	}{
		{"default limit", SearchRequest{Query: " a car "}, true},
		{"minimum", SearchRequest{Query: "car", Limit: ptr(1)}, true},
		{"maximum", SearchRequest{Query: "car", Limit: ptr(30)}, true},
		{"zero", SearchRequest{Query: "car", Limit: ptr(0)}, false},
		{"negative", SearchRequest{Query: "car", Limit: ptr(-1)}, false},
		{"too large", SearchRequest{Query: "car", Limit: ptr(31)}, false},
		{"blank", SearchRequest{Query: " \n "}, false},
		{"length boundary", SearchRequest{Query: strings.Repeat("車", 500)}, true},
		{"too long", SearchRequest{Query: strings.Repeat("車", 501)}, false},
		{"filter", SearchRequest{Query: "car", VideoID: ptr(uuid.NewString())}, true},
		{"bad filter", SearchRequest{Query: "car", VideoID: ptr("bad-id")}, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := tc.r.validate()
			if (e == nil) != tc.valid {
				t.Fatalf("valid=%v error=%v", tc.valid, e)
			}
			if tc.valid && tc.r.Limit == nil {
				t.Fatal("missing default limit")
			}
			if tc.name == "default limit" && (tc.r.Query != "a car" || *tc.r.Limit != 12) {
				t.Fatalf("wrong defaults: %+v", tc.r)
			}
		})
	}
}
func TestLegalEnqueueTransitions(t *testing.T) {
	for _, status := range []string{"awaiting_upload", "queued", "processing", "ready", "failed", "invalid"} {
		for _, retry := range []bool{false, true} {
			name := status + "/complete"
			if retry {
				name = status + "/retry"
			}
			t.Run(name, func(t *testing.T) {
				create, e := enqueueDecision(status, retry)
				wantCreate := (!retry && status == "awaiting_upload") || (retry && status == "failed")
				wantError := (retry && status != "failed") || (!retry && (status == "failed" || status == "invalid"))
				if create != wantCreate || (e != nil) != wantError {
					t.Fatalf("create=%v error=%v", create, e)
				}
			})
		}
	}
}
func unitVector() []float64 { x := make([]float64, 512); x[0] = 1; return x }
func TestEmbeddingValidation(t *testing.T) {
	for _, tc := range []struct {
		name    string
		vec     []float64
		version string
		valid   bool
	}{
		{"normalized", unitVector(), modelVersion, true},
		{"wrong model", unitVector(), "other", false},
		{"wrong dimensions", []float64{1}, modelVersion, false},
		{"zero vector", make([]float64, 512), modelVersion, false},
		{"unnormalized", func() []float64 { x := unitVector(); x[0] = 2; return x }(), modelVersion, false},
		{"NaN", func() []float64 { x := unitVector(); x[0] = math.NaN(); return x }(), modelVersion, false},
		{"infinity", func() []float64 { x := unitVector(); x[0] = math.Inf(1); return x }(), modelVersion, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if e := validateEmbedding(tc.vec, tc.version); (e == nil) != tc.valid {
				t.Fatalf("valid=%v error=%v", tc.valid, e)
			}
		})
	}
}
