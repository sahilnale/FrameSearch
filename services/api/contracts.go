package main

import (
	"context"
	"errors"
	"fmt"
	"math"

	"time"

	"github.com/google/uuid"
)

const maxUploadBytes int64 = 100 * 1024 * 1024
const modelVersion = "ViT-B-32:laion2b_s34b_b79k"
const urlExpiry = 900 * time.Second

var errNotFound = errors.New("not found")
var errConflict = errors.New("invalid status transition")
var errObjectMissing = errors.New("uploaded object not found")

type Video struct {
	ID              string    `json:"id"`
	Filename        string    `json:"filename"`
	Status          string    `json:"status"`
	Duration        *float64  `json:"duration_seconds"`
	ProcessingError *string   `json:"processing_error"`
	CreatedAt       time.Time `json:"created_at"`
	ObjectKey       string    `json:"-"`
	ContentType     string    `json:"-"`
	Size            int64     `json:"-"`
}
type UploadRequest struct {
	Filename    string `json:"filename"`
	ContentType string `json:"content_type"`
	Size        int64  `json:"size_bytes"`
}

type SearchRequest struct {
	Query   string  `json:"query"`
	Limit   *int    `json:"limit"`
	VideoID *string `json:"video_id"`
}

type SearchResult struct {
	VideoID      string  `json:"video_id"`
	FrameID      string  `json:"frame_id"`
	Filename     string  `json:"filename"`
	Timestamp    int     `json:"timestamp_ms"`
	ThumbnailURL string  `json:"thumbnail_url"`
	Similarity   float64 `json:"similarity"`
	ThumbnailKey string  `json:"-"`
}
type Job struct {
	ID      string
	VideoID string
}
type Event struct {
	EventID       string    `json:"event_id"`
	EventType     string    `json:"event_type"`
	SchemaVersion int       `json:"schema_version"`
	VideoID       string    `json:"video_id"`
	JobID         string    `json:"job_id"`
	CreatedAt     time.Time `json:"created_at"`
}

func newEvent(j Job) Event {
	return Event{uuid.NewString(), "media.uploaded", 1, j.VideoID, j.ID, time.Now().UTC()}
}

// The row lock and partial unique index in Store.Enqueue make this decision atomic.
func enqueueDecision(status string, retry bool) (bool, error) {
	if retry {
		if status == "failed" {
			return true, nil
		}
		return false, errConflict
	}
	switch status {
	case "awaiting_upload":
		return true, nil
	case "queued", "processing", "ready":
		return false, nil
	default:
		return false, errConflict
	}
}
func validateEmbedding(v []float64, version string) error {
	if version != modelVersion {
		return fmt.Errorf("processor model_version does not match %s", modelVersion)
	}
	if len(v) != 512 {
		return errors.New("processor must return 512 dimensions")
	}
	norm := 0.0
	for _, x := range v {
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return errors.New("embedding must contain finite numbers")
		}
		norm += x * x
	}
	if math.Abs(math.Sqrt(norm)-1) > 0.01 {
		return errors.New("embedding must be L2 normalized")
	}
	return nil
}

type Repository interface {
	Create(context.Context, Video) error
	Get(context.Context, string) (Video, error)
	List(context.Context) ([]Video, error)
	Enqueue(context.Context, string, bool) (Video, *Job, error)
	Search(context.Context, []float64, SearchRequest) ([]SearchResult, error)
	Ping(context.Context) error
}
type ObjectInfo struct {
	Size        int64
	ContentType string
}
type Storage interface {
	PutURL(context.Context, string) (string, error)
	GetURL(context.Context, string) (string, error)
	Head(context.Context, string) (ObjectInfo, error)
	Ping(context.Context) error
}
type Publisher interface {
	Publish(context.Context, Event) error
	Ping(context.Context) error
}
type Embedder interface {
	Embed(context.Context, string) ([]float64, string, error)
	Ping(context.Context) error
}
