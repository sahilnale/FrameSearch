package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"github.com/jackc/pgx/v5/pgxpool"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

func env(key, fallback string) string {
	if x := os.Getenv(key); x != "" {
		return x
	}
	return fallback
}
func validURL(raw string) bool {
	u, e := url.Parse(raw)
	return e == nil && u.Host != "" && (u.Scheme == "http" || u.Scheme == "https") && u.User == nil && u.RawQuery == "" && u.Fragment == ""
}
func run() error {
	reconcile := flag.Bool("reconcile", false, "republish queued jobs")
	stale := flag.Bool("include-stale", false, "also reset stale processing jobs; stop processor first")
	stopped := flag.Bool("processor-stopped", false, "confirm sole processor stopped before reclaiming")
	flag.Parse()
	if *stale && (!*reconcile || !*stopped) {
		return errors.New("--include-stale requires --reconcile --processor-stopped; stop all processors before recovery")
	}
	if env("MODEL_NAME", "ViT-B-32")+":"+env("MODEL_PRETRAINED", "laion2b_s34b_b79k") != modelVersion {
		return errors.New("model settings must match frozen model_version " + modelVersion)
	}
	db := os.Getenv("DATABASE_URL")
	if db == "" {
		return errors.New("DATABASE_URL is required")
	}
	for _, key := range []string{"S3_ACCESS_KEY", "S3_SECRET_KEY"} {
		if os.Getenv(key) == "" {
			return fmt.Errorf("%s is required", key)
		}
	}
	internal := env("S3_ENDPOINT_INTERNAL", "http://localhost:9000")
	public := env("S3_ENDPOINT_PUBLIC", "http://localhost:9000")
	processor := strings.TrimRight(env("PROCESSOR_URL", "http://localhost:8000"), "/")
	origin := env("WEB_ORIGIN", "http://localhost:3000")
	for _, v := range []string{internal, public, processor, origin} {
		if !validURL(v) {
			return errors.New("HTTP endpoints must be valid absolute http(s) URLs")
		}
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	cfg, e := pgxpool.ParseConfig(db)
	if e != nil {
		return e
	}
	cfg.MaxConns = 10
	cfg.ConnConfig.ConnectTimeout = 5 * time.Second
	pool, e := pgxpool.NewWithConfig(ctx, cfg)
	if e != nil {
		return e
	}
	defer pool.Close()
	store := &Store{pool}
	publisher := newPublisher(env("KAFKA_BROKERS", "localhost:9092"), env("KAFKA_TOPIC", "media.uploaded"))
	defer publisher.writer.Close()
	if *reconcile {
		age, e := time.ParseDuration(env("RECONCILE_STALE_AFTER", "15m"))
		if e != nil || age < time.Minute {
			return errors.New("RECONCILE_STALE_AFTER must be a duration >=1m")
		}
		recoveryCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()
		n, e := store.Reconcile(recoveryCtx, publisher, *stale, age)
		slog.Info("reconciliation", "published_jobs", n)
		return e
	}
	storage := newStorage(internal, public, os.Getenv("S3_ACCESS_KEY"), os.Getenv("S3_SECRET_KEY"), env("S3_BUCKET", "framesearch"))
	api := &API{store, storage, publisher, &ProcessorClient{processor, &http.Client{Timeout: 20 * time.Second}}, origin}
	server := &http.Server{Addr: env("API_ADDR", ":8080"), Handler: api.Handler(), ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 35 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32 * 1024}
	ch := make(chan error, 1)
	go func() {
		slog.Info("API listening", "addr", server.Addr, "model_version", modelVersion)
		ch <- server.ListenAndServe()
	}()
	select {
	case e := <-ch:
		if !errors.Is(e, http.ErrServerClosed) {
			return e
		}
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return server.Shutdown(shutdown)
	}
	return nil
}
func main() {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	if e := run(); e != nil {
		slog.Error("API stopped", "error", e)
		os.Exit(1)
	}
}
