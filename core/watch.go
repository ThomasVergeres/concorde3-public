package core

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"
)

// Optional observation capability implemented as an ordinary supervised Program.
// It observes bytes, not meaning; it neither reserves attention nor chooses goals.
type WatchSpec struct {
	Path            string            `json:"path,omitempty"`
	URL             string            `json:"url,omitempty"`
	Headers         map[string]string `json:"headers,omitempty"`
	IntervalSeconds int               `json:"interval_seconds"`
}
type WatchState struct {
	Generation  string    `json:"generation,omitempty"`
	Fingerprint string    `json:"fingerprint,omitempty"`
	Sequence    uint64    `json:"sequence"`
	At          time.Time `json:"at,omitempty"`
	Error       string    `json:"error,omitempty"`
}

func (w WatchSpec) Validate() error {
	if (w.Path == "") == (w.URL == "") || w.IntervalSeconds < 5 || w.IntervalSeconds > 3600 {
		return fmt.Errorf("watch requires exactly one file path or HTTP(S) URL and interval_seconds 5..3600")
	}
	if w.URL != "" {
		u, e := url.Parse(w.URL)
		if e != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
			return fmt.Errorf("watch URL must be HTTP(S)")
		}
	}
	if w.Path != "" && len(w.Headers) > 0 {
		return fmt.Errorf("headers apply only to HTTP watches")
	}
	if len(Marshal(w)) > 6000 {
		return fmt.Errorf("watch specification exceeds 6000 bytes")
	}
	return nil
}

func (s *Store) readWatch(ctx context.Context, w WatchSpec) ([]byte, error) {
	var source io.ReadCloser
	if w.Path != "" {
		fd, err := syscall.Open(w.Path, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW, 0)
		if err != nil {
			return nil, err
		}
		f := os.NewFile(uintptr(fd), w.Path)
		st, err := f.Stat()
		if err != nil {
			f.Close()
			return nil, err
		}
		if !st.Mode().IsRegular() {
			f.Close()
			return nil, fmt.Errorf("watch source must be a regular non-symlink file")
		}
		source = f
	} else {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, w.URL, nil)
		if err != nil {
			return nil, err
		}
		for k, v := range w.Headers {
			req.Header.Set(k, v)
		}
		client := &http.Client{Timeout: 15 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
		response, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		if response.StatusCode < 200 || response.StatusCode >= 300 {
			response.Body.Close()
			return nil, fmt.Errorf("watch HTTP status %d; no redirect or write performed", response.StatusCode)
		}
		source = response.Body
	}
	defer source.Close()
	body, err := io.ReadAll(io.LimitReader(source, (1<<20)+1))
	if err != nil {
		return nil, err
	}
	if len(body) > 1<<20 {
		return nil, fmt.Errorf("watch source exceeds 1 MiB; use a smaller stable observation")
	}
	return body, nil
}

func watchValue(body []byte, err error, at time.Time) WatchState {
	detail := ""
	if err != nil {
		truncated := false
		detail = clipContext(strings.ToValidUTF8(err.Error(), "�"), 400, &truncated)
	}
	return WatchState{Fingerprint: Digest(Marshal([]string{Digest(body), detail})), At: at, Error: detail}
}

func (s *Store) RegisterWatch(ctx context.Context, actor, id, intention, reason string, w WatchSpec) (any, error) {
	if err := s.CheckActor(actor); err != nil {
		return nil, err
	}
	st, err := s.Read()
	if err != nil {
		return nil, err
	}
	if !st.Config.Workspace {
		return nil, fmt.Errorf("watch requires workspace/program permission")
	}
	if intention == "" {
		intention = st.Activations[actor].Pursuit
		if actor == "operator" {
			intention = st.Config.Reconsideration
		}
	}
	if !validID(id) || reason == "" || st.Portfolio()[intention].ID == "" {
		return nil, fmt.Errorf("watch requires a valid program ID, reason and intention")
	}
	if old, ok := st.Programs[id]; ok && (old.Enabled || old.Process.PID != 0) {
		return nil, fmt.Errorf("stop program %s before replacing its watch", id)
	}
	if w.IntervalSeconds == 0 {
		w.IntervalSeconds = 15
	}
	if w.Path != "" && !filepath.IsAbs(w.Path) {
		w.Path = filepath.Join(s.Dir, w.Path)
	}
	if err = w.Validate(); err != nil {
		return nil, err
	}
	readCtx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	body, readErr := s.readWatch(readCtx, w)
	if readErr != nil {
		return nil, fmt.Errorf("initial watch observation failed; no program registered: %w", readErr)
	}
	initial := watchValue(body, nil, s.Now())
	initial.Generation = NewID("watch")
	exe, err := os.Executable()
	if err != nil {
		return nil, err
	}
	program := Program{ID: id, Command: []string{exe, "watch-source", s.Dir, id}, Pursuit: intention, Enabled: true, Status: "idle", Watch: &w, Observed: initial}
	err = s.Update(actor, "program.watch_registered", reason, func(st *State) error {
		if old, ok := st.Programs[id]; ok && (old.Enabled || old.Process.PID != 0) {
			return fmt.Errorf("watch changed concurrently; stop it before replacement")
		}
		if !st.Config.Workspace {
			return fmt.Errorf("workspace permission revoked")
		}
		st.Programs[id] = program
		return nil
	})
	if err != nil {
		return nil, err
	}
	truncated := false
	preview := clipContext(strings.ToValidUTF8(string(body), "�"), min(3000, st.Config.ResponseBytes/3), &truncated)
	return map[string]any{"registered": id, "intention": intention, "initial_observation": preview, "truncated": truncated, "fingerprint": initial.Fingerprint,
		"basis": "Initial contents returned, not treated as already handled. A supervised program will observe subsequent byte changes/fetch errors without quiet cognitive polling. Response still needs available attention; no delivery guarantee. Stop with program_stop."}, nil
}

// SampleWatch commits a changed observation and wake together. Sequence prevents
// an A→B→A change from being mistaken for an old delivery, including after restart.
func (s *Store) SampleWatch(ctx context.Context, id string) error {
	st, err := s.Read()
	if err != nil {
		return err
	}
	p, ok := st.Programs[id]
	if !ok || !p.Enabled || p.Watch == nil || st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
		return fmt.Errorf("watch is not active")
	}
	body, readErr := s.readWatch(ctx, *p.Watch)
	if ctx.Err() == context.Canceled {
		return ctx.Err() // Operator/supervisor cancellation is not a source failure.
	}
	current := watchValue(body, readErr, s.Now())
	if current.Fingerprint == p.Observed.Fingerprint {
		return nil
	}
	return s.Update("operator", "program.observed", "instance-owned source observation", func(st *State) error {
		latest := st.Programs[id]
		if !latest.Enabled || latest.Watch == nil || Digest(Marshal(latest.Watch)) != Digest(Marshal(p.Watch)) || st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
			return nil
		}
		if latest.Observed.Generation != p.Observed.Generation || latest.Observed.Sequence != p.Observed.Sequence {
			return nil // Replacement or a newer sample won while this read was in flight.
		}
		if latest.Observed.Fingerprint == current.Fingerprint {
			return nil
		}
		current.Sequence = latest.Observed.Sequence + 1
		current.Generation = latest.Observed.Generation
		latest.Observed = current
		st.Programs[id] = latest
		if st.Portfolio()[latest.Pursuit].Status == "stopped" {
			return nil
		}
		key, err := watchReference(st.Wakes, Digest(Marshal([]any{id, current.Generation, current.Sequence, current.Fingerprint})))
		if err != nil {
			return err // Never overwrite an unrelated observation on a reference collision.
		}
		source := latest.Watch.Path
		if source == "" {
			source = latest.Watch.URL
		}
		truncated := false
		source = clipContext(source, 500, &truncated)
		detail := "Source bytes changed; inspect the current source."
		if readErr != nil {
			detail = "Source observation failed: " + current.Error
		}
		st.Wakes[key] = Wake{ID: key, Pursuit: latest.Pursuit, At: s.Now(), Evidence: "Watch " + id + " (" + source + "): " + detail + " This observation is not a business-outcome verdict."}
		return nil
	})
}

// Operational references are handles, not source integrity hashes. Use 128 bits
// for ordinary handles; retain full fingerprints and leave all old IDs valid.
// An occupied short handle falls back to the full digest, without overwriting it.
func watchReference(wakes map[string]Wake, digest string) (string, error) {
	short := "watch." + digest[:32]
	if _, exists := wakes[short]; !exists {
		return short, nil
	}
	full := "watch." + digest
	if _, exists := wakes[full]; exists {
		return "", fmt.Errorf("watch reference collision; observation not committed")
	}
	return full, nil
}

func (s *Store) WatchLoop(ctx context.Context, id string) error {
	for {
		st, err := s.Read()
		if err != nil {
			return err
		}
		p, ok := st.Programs[id]
		if !ok || !p.Enabled || p.Watch == nil || st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
			return nil
		}
		sample, cancel := context.WithTimeout(ctx, 15*time.Second)
		err = s.SampleWatch(sample, id)
		cancel()
		if err != nil {
			return err
		}
		timer := time.NewTimer(time.Duration(p.Watch.IntervalSeconds) * time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil
		case <-timer.C:
		}
	}
}
