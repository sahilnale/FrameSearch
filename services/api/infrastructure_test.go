package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/google/uuid"
	"github.com/segmentio/kafka-go"
)

// This test verifies real object storage, DB enqueue and Kafka publication.
// Uploaded bytes are deliberately opaque test data; no decoding, processing,
// text inference, semantic relevance or complete video smoke test is claimed.
func TestInfrastructureUploadAndPublication(t *testing.T) {
	endpoint := os.Getenv("TEST_S3_ENDPOINT")
	brokers := os.Getenv("TEST_KAFKA_BROKERS")
	if endpoint == "" || brokers == "" || os.Getenv("TEST_DATABASE_URL") == "" {
		t.Skip("set TEST_DATABASE_URL, TEST_S3_ENDPOINT and TEST_KAFKA_BROKERS for real infrastructure tests")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	store := integrationStore(t)
	bucket := "framesearch-test-" + uuid.NewString()
	storage := newStorage(endpoint, endpoint, env("TEST_S3_ACCESS_KEY", "framesearch"), env("TEST_S3_SECRET_KEY", "framesearch-local-secret"), bucket)
	if _, e := storage.internal.CreateBucket(ctx, &s3.CreateBucketInput{Bucket: aws.String(bucket)}); e != nil {
		t.Fatal(e)
	}
	key := ""
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if key != "" {
			if _, e := storage.internal.DeleteObject(ctx, &s3.DeleteObjectInput{Bucket: aws.String(bucket), Key: aws.String(key)}); e != nil {
				t.Error(e)
			}
		}
		if _, e := storage.internal.DeleteBucket(ctx, &s3.DeleteBucketInput{Bucket: aws.String(bucket)}); e != nil {
			t.Error(e)
		}
	})
	publisher := newPublisher(brokers, env("TEST_KAFKA_TOPIC", "media.uploaded"))
	defer publisher.writer.Close()
	if e := publisher.Ping(ctx); e != nil {
		t.Fatal(e)
	}
	// Search is not invoked; this dependency is irrelevant to this test.
	a := &API{store, storage, publisher, &testEmbedder{}, "http://localhost:3000"}
	server := httptest.NewServer(a.Handler())
	defer server.Close()
	client := &http.Client{Timeout: 10 * time.Second}
	post := func(path string, body []byte) *http.Response {
		t.Helper()
		req, e := http.NewRequestWithContext(ctx, "POST", server.URL+path, bytes.NewReader(body))
		if e != nil {
			t.Fatal(e)
		}
		req.Header.Set("Content-Type", "application/json")
		res, e := client.Do(req)
		if e != nil {
			t.Fatal(e)
		}
		return res
	}
	payload := []byte("opaque object bytes for backend contract verification")
	body, _ := json.Marshal(UploadRequest{"fixture.mp4", "video/mp4", int64(len(payload))})
	res := post("/api/v1/videos/upload-url", body)
	var reservation struct {
		VideoID string `json:"video_id"`
		URL     string `json:"upload_url"`
		Key     string `json:"object_key"`
	}
	if res.StatusCode != 201 {
		b, _ := io.ReadAll(res.Body)
		res.Body.Close()
		t.Fatalf("reservation status=%d body=%s", res.StatusCode, b)
	}
	if e := json.NewDecoder(res.Body).Decode(&reservation); e != nil {
		res.Body.Close()
		t.Fatal(e)
	}
	res.Body.Close()
	key = reservation.Key
	req, _ := http.NewRequestWithContext(ctx, "OPTIONS", reservation.URL, nil)
	req.Header.Set("Origin", a.origin)
	req.Header.Set("Access-Control-Request-Method", "PUT")
	req.Header.Set("Access-Control-Request-Headers", "content-type")
	res, e := client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if res.StatusCode < 200 || res.StatusCode >= 300 || res.Header.Get("Access-Control-Allow-Origin") != a.origin {
		t.Fatalf("MinIO CORS failed: status=%d origin=%s", res.StatusCode, res.Header.Get("Access-Control-Allow-Origin"))
	}
	req, _ = http.NewRequestWithContext(ctx, "PUT", reservation.URL, bytes.NewReader(payload))
	req.Header.Set("Content-Type", "video/mp4")
	res, e = client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if res.StatusCode != 200 {
		t.Fatalf("signed PUT status=%d", res.StatusCode)
	}
	for i := 0; i < 2; i++ {
		res = post("/api/v1/videos/"+reservation.VideoID+"/complete", nil)
		b, _ := io.ReadAll(res.Body)
		res.Body.Close()
		if res.StatusCode != 200 {
			t.Fatalf("complete status=%d body=%s", res.StatusCode, b)
		}
	}
	var count int
	var jobID string
	if e = store.pool.QueryRow(ctx, "SELECT count(*) FROM processing_jobs WHERE video_id=$1", reservation.VideoID).Scan(&count); e != nil || count != 1 {
		t.Fatalf("active job count=%d error=%v", count, e)
	}
	if e = store.pool.QueryRow(ctx, "SELECT id::text FROM processing_jobs WHERE video_id=$1", reservation.VideoID).Scan(&jobID); e != nil {
		t.Fatal(e)
	}
	reader := kafka.NewReader(kafka.ReaderConfig{Brokers: strings.Split(brokers, ","), Topic: publisher.topic, Partition: 0, MinBytes: 1, MaxBytes: 1e6, MaxWait: time.Second})
	defer reader.Close()
	for {
		m, e := reader.ReadMessage(ctx)
		if e != nil {
			t.Fatal(e)
		}
		if string(m.Key) != reservation.VideoID {
			continue
		}
		var event Event
		if e = json.Unmarshal(m.Value, &event); e != nil {
			t.Fatal(e)
		}
		if event.JobID != jobID || event.EventType != "media.uploaded" || event.SchemaVersion != 1 {
			t.Fatalf("invalid real event: %+v", event)
		}
		break
	}
	signed, e := storage.GetURL(ctx, key)
	if e != nil {
		t.Fatal(e)
	}
	req, _ = http.NewRequestWithContext(ctx, "GET", signed, nil)
	req.Header.Set("Range", "bytes=0-3")
	res, e = client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	b, e := io.ReadAll(res.Body)
	res.Body.Close()
	if e != nil || res.StatusCode != 206 || !bytes.Equal(b, payload[:4]) {
		t.Fatalf("range playback status=%d bytes=%q error=%v", res.StatusCode, b, e)
	}
	unsigned, _ := url.Parse(signed)
	unsigned.RawQuery = ""
	req, _ = http.NewRequestWithContext(ctx, "GET", unsigned.String(), nil)
	res, e = client.Do(req)
	if e != nil {
		t.Fatal(e)
	}
	res.Body.Close()
	if res.StatusCode != 403 {
		t.Fatalf("unsigned object should remain private: status=%d", res.StatusCode)
	}
}
