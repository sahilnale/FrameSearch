package main

import (
	"context"

	"errors"

	"net/http"

	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	"github.com/aws/aws-sdk-go-v2/service/s3/types"
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
