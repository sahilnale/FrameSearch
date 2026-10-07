package main

func ptr[T any](x T) *T { return &x }

func unitVector() []float64 { x := make([]float64, 512); x[0] = 1; return x }
