package main

import (
	"math"

	"testing"
)

func ptr[T any](x T) *T { return &x }

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
