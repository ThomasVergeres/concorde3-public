package core

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func watchFixture(t *testing.T) (*Store, string) {
	t.Helper()
	s := fixture(t)
	if err := s.Update("operator", "fixture", "watch capability", func(st *State) error { st.Config.Workspace = true; return nil }); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(s.Dir, "inbox.json")
	if err := os.WriteFile(path, []byte("initial"), 0600); err != nil {
		t.Fatal(err)
	}
	running(t, s)
	return s, path
}

func TestWatchRectificationAdmissionRestartAndABA(t *testing.T) {
	s, path := watchFixture(t)
	a, _, err := s.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if err = s.BeginRectification(a.ID, "work ended", "test"); err != nil {
		t.Fatal(err)
	}
	v, err := s.Call(context.Background(), a.ID, "watch", Marshal(map[string]any{"id": "inbox", "reason": "observe actual incoming work", "path": path}))
	if err != nil || v.(map[string]any)["initial_observation"] != "initial" {
		t.Fatal(v, err)
	}
	complete(t, s, a, "dormant")
	if err = s.SampleWatch(context.Background(), "inbox"); err != nil {
		t.Fatal(err)
	}
	st, _ := s.Read()
	if len(st.Wakes) != 0 {
		t.Fatal("quiet observation consumed attention")
	}
	for i, body := range []string{"changed", "initial"} {
		if err = os.WriteFile(path, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
		s = Open(s.Dir)
		s.Now = func() time.Time { return a.Started.Add(time.Minute) }
		if err = s.SampleWatch(context.Background(), "inbox"); err != nil {
			t.Fatal(err)
		}
		st, _ = s.Read()
		if len(st.Wakes) != i+1 || st.Programs["inbox"].Observed.Sequence != uint64(i+1) {
			t.Fatal("lost restart/ABA observation", st.Wakes)
		}
		b, p, e := s.Admit()
		if e != nil || len(p.Wakes) == 0 {
			t.Fatal("no real admission", e)
		}
		complete(t, s, b, "dormant")
	}
	if err = os.Remove(path); err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if err = s.SampleWatch(context.Background(), "inbox"); err != nil {
			t.Fatal(err)
		}
	}
	st, _ = s.Read()
	if len(st.Wakes) != 3 || st.Programs["inbox"].Observed.Error == "" {
		t.Fatal("read failure hidden or repeated")
	}
	if err = os.WriteFile(path, []byte("recovered"), 0600); err != nil {
		t.Fatal(err)
	}
	if err = s.SampleWatch(context.Background(), "inbox"); err != nil {
		t.Fatal(err)
	}
	st, _ = s.Read()
	if len(st.Wakes) != 4 || st.Programs["inbox"].Observed.Error != "" {
		t.Fatal("recovery missing")
	}
	if err = s.StopProgram("inbox"); err != nil {
		t.Fatal(err)
	}
	if err = s.SampleWatch(context.Background(), "inbox"); err == nil {
		t.Fatal("stopped watch ran")
	}
}

func TestWatchHTTPBoundsAndReplacementRace(t *testing.T) {
	s, _ := watchFixture(t)
	var mode atomic.Int32
	entered, release := make(chan struct{}), make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" || r.Header.Get("X-Test") != "private-test" {
			t.Error("wrong observation request")
		}
		switch mode.Load() {
		case 1:
			close(entered)
			<-release
			_, _ = w.Write([]byte("stale"))
		case 2:
			http.Redirect(w, r, "/elsewhere", http.StatusFound)
		case 3:
			_, _ = w.Write([]byte(strings.Repeat("x", (1<<20)+1)))
		default:
			_, _ = w.Write([]byte("initial"))
		}
	}))
	defer server.Close()
	spec := WatchSpec{URL: server.URL, Headers: map[string]string{"X-Test": "private-test"}, IntervalSeconds: 5}
	register := func() {
		t.Helper()
		if _, e := s.RegisterWatch(context.Background(), "operator", "http", "purpose", "observe", spec); e != nil {
			t.Fatal(e)
		}
	}
	register()
	mode.Store(1)
	done := make(chan error, 1)
	go func() { done <- s.SampleWatch(context.Background(), "http") }()
	<-entered
	if e := s.StopProgram("http"); e != nil {
		t.Fatal(e)
	}
	mode.Store(0)
	register()
	close(release)
	if e := <-done; e != nil {
		t.Fatal(e)
	}
	st, _ := s.Read()
	if len(st.Wakes) != 0 {
		t.Fatal("old generation overwrote replacement")
	}
	for _, m := range []int32{2, 3} {
		mode.Store(m)
		if _, e := s.readWatch(context.Background(), spec); e == nil {
			t.Fatal("redirect or oversized input accepted")
		}
	}
	if e := s.Freeze("test cutoff"); e != nil {
		t.Fatal(e)
	}
	if e := s.SampleWatch(context.Background(), "http"); e == nil {
		t.Fatal("watch ignored freeze")
	}
}

func TestWatchRejectsUnusableInitialSource(t *testing.T) {
	s, path := watchFixture(t)
	link := filepath.Join(s.Dir, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	for _, p := range []string{link, s.Dir, filepath.Join(s.Dir, "absent")} {
		if _, err := s.RegisterWatch(context.Background(), "operator", "bad", "purpose", "observe", WatchSpec{Path: p, IntervalSeconds: 5}); err == nil {
			t.Fatal("unusable source accepted", p)
		}
	}
	st, _ := s.Read()
	if len(st.Programs) != 0 {
		t.Fatal("failed initial read registered program")
	}
}

func TestCanceledWatchSampleDoesNotManufactureAnObservation(t *testing.T) {
	s, _ := watchFixture(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write([]byte("quiet")) }))
	defer server.Close()
	if _, err := s.RegisterWatch(context.Background(), "operator", "cancel", "purpose", "observe", WatchSpec{URL: server.URL, IntervalSeconds: 5}); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_ = s.SampleWatch(ctx, "cancel")
	st, _ := s.Read()
	if len(st.Wakes) != 0 || st.Programs["cancel"].Observed.Error != "" {
		t.Fatal("supervisor cancellation became an external-source failure")
	}
}

func TestWatchActualCLIProgramAndStop(t *testing.T) {
	s, path := watchFixture(t)
	binary := filepath.Join(t.TempDir(), "concorde3")
	cmd := exec.Command("go", "build", "-o", binary, "../cmd/concorde3")
	if out, err := cmd.CombinedOutput(); err != nil {
		t.Fatal(string(out), err)
	}
	if _, err := s.RegisterWatch(context.Background(), "operator", "actual", "purpose", "observe", WatchSpec{Path: path, IntervalSeconds: 5}); err != nil {
		t.Fatal(err)
	}
	// Registration in a test process uses the test executable; use the actual CLI
	// at precisely that launch boundary, preserving the generated arguments.
	if err := s.Update("operator", "fixture", "actual compiled CLI", func(st *State) error {
		p := st.Programs["actual"]
		p.Command[0] = binary
		st.Programs[p.ID] = p
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("new"), 0600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	done := make(chan error, 1)
	go func() { done <- s.RunProgram(ctx, "actual") }()
	deadline := time.Now().Add(8 * time.Second)
	for {
		st, _ := s.Read()
		if len(st.Wakes) > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("supervised CLI failed to observe")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err := s.StopProgram("actual"); err != nil {
		t.Fatal(err)
	}
	select {
	case <-done:
	case <-ctx.Done():
		t.Fatal("watch process did not stop")
	}
	st, _ := Open(s.Dir).Read()
	if st.Programs["actual"].Process.PID != 0 || st.Programs["actual"].Enabled {
		t.Fatal("watch leaked past stop")
	}
}
