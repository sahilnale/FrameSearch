package main

import (
	"context"
	"encoding/json"
	"errors"

	"net/http"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
	"github.com/segmentio/kafka-go"
)

type S3Storage struct {
	internal *s3.Client
	signer   *s3.PresignClient
	bucket   string
}

func newStorage(internal, public, access, secret, bucket string) *S3Storage {
	cfg := aws.Config{Region: "us-east-1", Credentials: credentials.NewStaticCredentialsProvider(access, secret, ""), HTTPClient: &http.Client{Timeout: 15 * time.Second}}
	makeClient := func(endpoint string) *s3.Client {
		return s3.NewFromConfig(cfg, func(o *s3.Options) { o.BaseEndpoint = aws.String(endpoint); o.UsePathStyle = true })
	}
	return &S3Storage{makeClient(internal), s3.NewPresignClient(makeClient(public)), bucket}
}
func (s *S3Storage) PutURL(ctx context.Context, key string) (string, error) {
	x, e := s.signer.PresignPutObject(ctx, &s3.PutObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(key), ContentType: aws.String("video/mp4")}, func(o *s3.PresignOptions) { o.Expires = urlExpiry })
	if e != nil {
		return "", e
	}
	return x.URL, nil
}
func (s *S3Storage) GetURL(ctx context.Context, key string) (string, error) {
	x, e := s.signer.PresignGetObject(ctx, &s3.GetObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(key)}, func(o *s3.PresignOptions) { o.Expires = urlExpiry })
	if e != nil {
		return "", e
	}
	return x.URL, nil
}
func (s *S3Storage) Head(ctx context.Context, key string) (ObjectInfo, error) {
	x, e := s.internal.HeadObject(ctx, &s3.HeadObjectInput{Bucket: aws.String(s.bucket), Key: aws.String(key)})
	if e != nil {
		var nf *types.NotFound
		var resp interface{ HTTPStatusCode() int }
		if errors.As(e, &nf) || (errors.As(e, &resp) && resp.HTTPStatusCode() == 404) {
			return ObjectInfo{}, errObjectMissing
		}
		return ObjectInfo{}, e
	}
	return ObjectInfo{aws.ToInt64(x.ContentLength), aws.ToString(x.ContentType)}, nil
}
func (s *S3Storage) Ping(ctx context.Context) error {
	_, e := s.internal.HeadBucket(ctx, &s3.HeadBucketInput{Bucket: aws.String(s.bucket)})
	return e
}

type KafkaPublisher struct {
	writer  *kafka.Writer
	brokers []string
	topic   string
}

func newPublisher(brokers, topic string) *KafkaPublisher {
	list := strings.Split(brokers, ",")
	return &KafkaPublisher{&kafka.Writer{Addr: kafka.TCP(list...), Topic: topic, Balancer: &kafka.Hash{}, RequiredAcks: kafka.RequireAll, Async: false, WriteTimeout: 10 * time.Second, ReadTimeout: 10 * time.Second, MaxAttempts: 3, BatchTimeout: 10 * time.Millisecond}, list, topic}
}
func (p *KafkaPublisher) Publish(ctx context.Context, event Event) error {
	b, e := json.Marshal(event)
	if e != nil {
		return e
	}
	return p.writer.WriteMessages(ctx, kafka.Message{Key: []byte(event.VideoID), Value: b})
}
func (p *KafkaPublisher) Ping(ctx context.Context) error {
	conn, e := (&kafka.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "tcp", p.brokers[0])
	if e != nil {
		return e
	}
	defer conn.Close()
	deadline := time.Now().Add(3 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	conn.SetDeadline(deadline)
	partitions, e := conn.ReadPartitions(p.topic)
	if e != nil {
		return e
	}
	if len(partitions) == 0 {
		return errors.New("Kafka topic has no partitions")
	}
	return nil
}
