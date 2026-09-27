package core

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

func (s *Store) RecordEffect(actor, key, fingerprint, status, detail string) (Receipt, error) {
	var receipt Receipt
	if !validID(key) || fingerprint == "" || len(detail) > 4000 {
		return receipt, fmt.Errorf("valid key, fingerprint and bounded receipt detail required")
	}
	if e := s.CheckActor(actor); e != nil {
		return receipt, e
	}
	e := s.Update(actor, "effect.receipt", "external effect "+status, func(st *State) error {
		old, exists := st.Receipts[key]
		if exists && old.Fingerprint != fingerprint {
			return fmt.Errorf("idempotency key collision")
		}
		if status == "reserved" {
			if exists {
				receipt = old
				return nil
			}
			if st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
				return fmt.Errorf("frozen")
			}
			receipt = Receipt{Key: key, Fingerprint: fingerprint, Status: status, Actor: actor, At: s.Now(), Detail: detail}
		} else {
			if !exists {
				return fmt.Errorf("reserve before performing an effect")
			}
			if status != "completed" && status != "uncertain" && status != "failed" {
				return fmt.Errorf("invalid receipt status")
			}
			if old.Status == "completed" {
				if status != "completed" || detail != old.Detail {
					return fmt.Errorf("completed receipt is immutable")
				}
				receipt = old
				return nil
			}
			receipt = old
			receipt.Status = status
			receipt.Detail = detail
		}
		st.Receipts[key] = receipt
		return nil
	})
	return receipt, e
}
func (s *Store) HTTPEffect(ctx context.Context, actor, key, method, target, body string, headers map[string]string) (any, error) {
	if e := s.CheckActor(actor); e != nil {
		return nil, e
	}
	if !validID(key) {
		return nil, fmt.Errorf("invalid idempotency key")
	}
	u, e := url.Parse(target)
	if e != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
		return nil, fmt.Errorf("HTTP(S) URL without embedded credentials required")
	}
	if method != "POST" && method != "PUT" && method != "PATCH" && method != "DELETE" {
		return nil, fmt.Errorf("unsupported effect method")
	}
	original, _ := json.Marshal(map[string]any{"method": method, "url": target, "body": body, "headers": headers})
	fingerprint := Digest(original)
	var receipt Receipt
	reserved := false
	e = s.Update(actor, "effect.reserved", "reserve HTTP effect before transmission", func(st *State) error {
		if st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
			return fmt.Errorf("frozen")
		}
		if old, ok := st.Receipts[key]; ok {
			if old.Fingerprint != fingerprint {
				return fmt.Errorf("idempotency collision")
			}
			receipt = old
			return nil
		}
		receipt = Receipt{Key: key, Fingerprint: fingerprint, Status: "reserved", Actor: actor, At: s.Now()}
		st.Receipts[key] = receipt
		reserved = true
		return nil
	})
	if e != nil {
		return nil, e
	}
	if !reserved {
		return map[string]any{"receipt": receipt, "sent": false, "instruction": "Existing intent/result: reconcile uncertain delivery; do not blindly repeat."}, nil
	}
	c, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(c, method, target, strings.NewReader(body))
	if e != nil {
		return nil, e
	}
	for k, v := range headers {
		if strings.HasPrefix(v, "env:") {
			env, err := s.InstanceEnvironment()
			if err != nil {
				return nil, err
			}
			v = env[strings.TrimPrefix(v, "env:")]
			if v == "" {
				return nil, fmt.Errorf("missing instance credential header")
			}
		}
		req.Header.Set(k, v)
	}
	req.Header.Set("Idempotency-Key", key)
	client := &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	resp, requestErr := client.Do(req)
	responseBody := ""
	status := 0
	if resp != nil {
		status = resp.StatusCode
		b, _ := io.ReadAll(io.LimitReader(resp.Body, 8000))
		_ = resp.Body.Close()
		responseBody = string(b)
	}
	// Transport errors and non-success responses may follow a provider-side effect.
	receipt.Status = "uncertain"
	if requestErr == nil && status >= 200 && status < 300 {
		receipt.Status = "completed"
	}
	receipt.HTTPStatus = status
	receipt.Detail = fmt.Sprintf("HTTP status %d; response_sha256=%s", status, Digest([]byte(responseBody)))
	e = s.Update("operator", "effect.finished", "record observed HTTP outcome", func(st *State) error { st.Receipts[key] = receipt; return nil })
	if e != nil {
		return nil, e
	}
	return map[string]any{"receipt": receipt, "sent": true, "response": responseBody, "transport_error": requestErr != nil}, nil
}
