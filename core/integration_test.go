package core

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestEffectReplayCollisionAndUncertain(t *testing.T) {
	s := fixture(t)
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("Idempotency-Key") == "" {
			t.Error("missing key")
		}
		if r.URL.Path == "/uncertain" {
			w.WriteHeader(500)
		}
		_, _ = w.Write([]byte("receipt"))
	}))
	defer server.Close()
	for i := 0; i < 2; i++ {
		if _, e := s.HTTPEffect(context.Background(), "operator", "one", "POST", server.URL, "payload", nil); e != nil {
			t.Fatal(e)
		}
	}
	if calls.Load() != 1 {
		t.Fatal("duplicate effect")
	}
	if _, e := s.HTTPEffect(context.Background(), "operator", "one", "POST", server.URL, "other", nil); e == nil {
		t.Fatal("accepted collision")
	}
	for i := 0; i < 2; i++ {
		if _, e := s.HTTPEffect(context.Background(), "operator", "unknown", "POST", server.URL+"/uncertain", "payload", nil); e != nil {
			t.Fatal(e)
		}
	}
	if calls.Load() != 2 {
		t.Fatal("retried uncertain effect")
	}
	st, _ := s.Read()
	if st.Receipts["unknown"].Status != "uncertain" {
		t.Fatal("uncertain effect mislabeled")
	}
	if e := s.Freeze("test freeze"); e != nil {
		t.Fatal(e)
	}
	if _, e := s.HTTPEffect(context.Background(), "operator", "after-freeze", "POST", server.URL, "", nil); e == nil {
		t.Fatal("effect after freeze")
	}
}
func TestReceiptBeforeEffectAndRestart(t *testing.T) {
	s := fixture(t)
	if _, e := s.RecordEffect("operator", "x", "digest", "completed", "made up"); e == nil {
		t.Fatal("unreserved effect accepted")
	}
	r, e := s.RecordEffect("operator", "x", "digest", "reserved", "intent")
	if e != nil {
		t.Fatal(e)
	}
	s = Open(s.Dir)
	got, e := s.RecordEffect("operator", "x", "digest", "reserved", "retry")
	if e != nil || got.At != r.At {
		t.Fatal("intent duplicated", e)
	}
}
func TestMCPInterfaceAndBounds(t *testing.T) {
	s := fixture(t)
	input := strings.Join([]string{`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}`, `{"jsonrpc":"2.0","id":2,"method":"tools/list"}`, `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"mutate","arguments":{"reason":"durable item","changes":{"items":[{"expected_revision":0,"item":{"id":"via-mcp","node":"undertaking","kind":"observation","text":"Survives restart","status":"active"}}]}}}}`}, "\n") + "\n"
	var out bytes.Buffer
	if e := s.MCP(context.Background(), strings.NewReader(input), &out, "operator"); e != nil {
		t.Fatal(e)
	}
	st, e := Open(s.Dir).Read()
	if e != nil || st.Items["via-mcp"].ID == "" {
		t.Fatal("MCP write lost", out.String(), e)
	}
}

func TestLargeRecordPagingAndArtifactIsolation(t *testing.T) {
	s := fixture(t)
	i := itemFixture("evidence", "Referenced large evidence")
	i.Sources = []Source{{Ref: "artifacts/source.txt"}}
	if e := s.Mutate("operator", "evidence", Batch{Items: []ItemChange{{Item: i}}}); e != nil {
		t.Fatal(e)
	}
	offset := 0
	var text strings.Builder
	for {
		v, e := s.Call(context.Background(), "operator", "record", Marshal(map[string]any{"section": "items", "id": "evidence", "offset": offset, "limit": 16}))
		if e != nil {
			t.Fatal(e)
		}
		m := v.(map[string]any)
		text.WriteString(m["text"].(string))
		offset = m["next_offset"].(int)
		if offset < 0 {
			break
		}
	}
	var got Item
	if json.Unmarshal([]byte(text.String()), &got) != nil || got.Sources[0].Ref != i.Sources[0].Ref {
		t.Fatal("lost reference")
	}
	if _, e := s.ArtifactPath("../brain.json"); e == nil {
		t.Fatal("traversal accepted")
	}
	_ = os.MkdirAll(filepath.Join(s.Dir, "artifacts"), 0700)
	_ = os.Symlink(s.path(""), filepath.Join(s.Dir, "artifacts", "escape"))
	if _, e := s.ArtifactPath("escape/state.json"); e == nil {
		t.Fatal("symlink escaped")
	}
}

func TestEmbeddingHelper(t *testing.T) {
	if len(os.Args) == 0 || os.Args[len(os.Args)-1] != "embedding-fixture" {
		return
	}
	b, _ := io.ReadAll(os.Stdin)
	var q struct {
		Texts []string `json:"texts"`
	}
	_ = json.Unmarshal(b, &q)
	vectors := [][]float64{}
	for _, text := range q.Texts {
		if strings.Contains(text, "archives") || strings.Contains(text, "historical retention") {
			vectors = append(vectors, []float64{1, 0})
		} else {
			vectors = append(vectors, []float64{0, 1})
		}
	}
	_ = json.NewEncoder(os.Stdout).Encode(map[string]any{"vectors": vectors})
	os.Exit(0)
}
func TestSemanticAdapterStalenessAndFallback(t *testing.T) {
	s := fixture(t)
	if e := s.Update("operator", "config", "synthetic embedding transport qualification", func(st *State) error {
		st.Config.EmbeddingCommand = []string{os.Args[0], "-test.run=TestEmbeddingHelper", "embedding-fixture"}
		return nil
	}); e != nil {
		t.Fatal(e)
	}
	n := itemFixture("distant", "historical retention needs independent testing")
	if e := s.Mutate("operator", "record lesson", Batch{Items: []ItemChange{{Item: n}}}); e != nil {
		t.Fatal(e)
	}
	if e := s.Reindex(context.Background()); e != nil {
		t.Fatal(e)
	}
	r, e := s.Search(context.Background(), "archives", 0, 5)
	if e != nil || len(r.Hits) == 0 || r.Hits[0].ID != "distant" {
		t.Fatal(r, e)
	}
	n.Text = "Unrelated replacement"
	if e := s.Mutate("operator", "revise", Batch{Items: []ItemChange{{ExpectedRevision: 1, Item: n}}}); e != nil {
		t.Fatal(e)
	}
	r, e = s.Search(context.Background(), "archives", 0, 5)
	if e != nil || r.StaleEntries != 1 {
		t.Fatal(r, e)
	}
	for _, h := range r.Hits {
		if h.ID == "distant" {
			t.Fatal("stale semantic truth")
		}
	}
	if e = os.Remove(s.path("search-index.json")); e != nil {
		t.Fatal(e)
	}
	r, e = s.Search(context.Background(), "replacement", 0, 5)
	if e != nil || !strings.Contains(r.Semantic, "unavailable") || len(r.Hits) == 0 {
		t.Fatal(r, e)
	}
}
func TestActualCommandLoop(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if e := s.Update("operator", "config", "actual protocol-2 harness", func(st *State) error {
		st.Config.Harness = "command"
		st.Config.Command = []string{os.Args[0], "-test.run=TestCommandHelper", "command-fixture"}
		return nil
	}); e != nil {
		t.Fatal(e)
	}
	if e := s.Run(context.Background(), true, nil); e != nil {
		t.Fatal(e)
	}
	st, e := Open(s.Dir).Read()
	if e != nil || st.Items["subprocess"].ID == "" {
		t.Fatal(e)
	}
	for _, a := range st.Activations {
		if a.Status != "completed" || a.Completion == nil {
			t.Fatal(a)
		}
	}
}
func TestCommandHelper(t *testing.T) {
	if !strings.Contains(strings.Join(os.Args, " "), "command-fixture") {
		return
	}
	if os.Args[len(os.Args)-1] == "--concorde-capabilities" {
		fmt.Print(`{"protocol":2,"continuation":true}`)
		os.Exit(0)
	}
	var p Packet
	_ = json.NewDecoder(os.Stdin).Decode(&p)
	if p.Phase == "work" {
		_ = json.NewEncoder(os.Stdout).Encode(Outcome{Summary: "actual work", Changes: Batch{Items: []ItemChange{{Item: itemFixture("subprocess", "Actual subprocess evidence")}}}})
	} else {
		st, _ := Open(os.Getenv("CONCORDE3_INSTANCE")).Read()
		p.Seq = st.Seq
		_ = json.NewEncoder(os.Stdout).Encode(Outcome{Summary: "actual rectification", Completion: &Completion{ExpectedSeq: p.Seq, Continuation: "wait", Coverage: "test task finished; no observer required", Reason: "adequate"}})
	}
	os.Exit(0)
}

func TestSupervisorFreezeStopsActualProcesses(t *testing.T) {
	s := fixture(t)
	s.Now = func() time.Time { return time.Now().UTC() }
	if e := s.Update("operator", "config", "finite subprocess qualification", func(st *State) error {
		st.Config.Workspace = true
		st.Config.Harness = "command"
		st.Config.Command = []string{"sleep", "30"}
		st.Config.FreezeAt = s.Now().Add(2 * time.Second)
		st.Programs["worker"] = Program{ID: "worker", Command: []string{"sleep", "30"}, Pursuit: "purpose", Enabled: true}
		return nil
	}); e != nil {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	if e := s.Run(ctx, false, nil); e != nil {
		t.Fatal(e)
	}
	st, e := s.Read()
	if e != nil || st.Mode != "frozen" {
		t.Fatal(st.Mode, e)
	}
	for _, a := range st.Activations {
		if Alive(a.Process) || a.Status == "running" {
			t.Fatal("live activation after freeze")
		}
	}
	for _, p := range st.Programs {
		if Alive(p.Process) || p.Enabled {
			t.Fatal("live program after freeze")
		}
	}
	if _, _, e = s.Admit(); e == nil {
		t.Fatal("admission after freeze")
	}
	if e = s.SetMode("running"); e == nil {
		t.Fatal("terminal freeze reopened")
	}
}
func TestRecoveryAndLateWriteback(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	if e := s.Recover(); e != nil {
		t.Fatal(e)
	}
	st, _ := s.Read()
	if st.Activations[a.ID].Phase != "rectification_pending" {
		t.Fatal("orphan not recoverable")
	}
	now := s.Now().Add(2 * time.Minute)
	s.Now = func() time.Time { return now }
	b, _, e := s.Admit()
	if e != nil {
		t.Fatal(e)
	}
	s.Now = func() time.Time { return b.Deadline.Add(time.Second) }
	if e = s.Mutate(b.ID, "late", Batch{Items: []ItemChange{{Item: itemFixture("late", "must not apply")}}}); e == nil {
		t.Fatal("expired write")
	}
}

func TestEventFloodPreservesBroadAndOrdinaryShares(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	fund(&st, "purpose", .2, "ready")
	fund(&st, "normal", .4, "ready")
	fund(&st, "noisy", .4, "ready")
	counts := map[string]int{}
	for j := 0; j < 1000; j++ {
		st.Wakes["flood"] = Wake{ID: "flood", Pursuit: "noisy", At: s.Now()}
		p, _, e := selectPursuit(&st, s.Now())
		if e != nil {
			t.Fatal(e)
		}
		counts[p.ID]++
		st.Starts++
	}
	if counts["purpose"] < 190 || counts["normal"] < 390 {
		t.Fatal(counts)
	}
}

func TestGrowthBounds(t *testing.T) {
	for _, count := range []int{1000, 10000} {
		t.Run(fmt.Sprint(count), func(t *testing.T) {
			s := fixture(t)
			start := time.Now()
			// Accumulate genuine committed revisions before growing the current
			// graph. A large current graph alone is not an archive-growth probe.
			const revisions = 128
			claim := itemFixture("archived-claim", "")
			claim.Node = "revision-archive"
			claim.Sources = []Source{{Ref: "fixture:original-source", ObservedAt: s.Now()}}
			for revision := 1; revision <= revisions; revision++ {
				claim.Text = fmt.Sprintf("Revision %d: ", revision) + strings.Repeat("歴史 é 🧭; ", 80)
				batch := Batch{Items: []ItemChange{{ExpectedRevision: revision - 1, Item: claim}}}
				if revision == 1 {
					batch.Nodes = []NodeChange{{Node: Node{ID: claim.Node, Title: "Retained revisions", Status: "active"}}}
				}
				if err := s.Mutate("operator", fmt.Sprintf("source reconsidered %d", revision), batch); err != nil {
					t.Fatal(err)
				}
			}
			archiveBuild := time.Since(start)
			if e := s.Update("operator", "benchmark", "bounded graph fixture", func(st *State) error {
				for j := 0; j < count; j++ {
					id := fmt.Sprintf("n.%d", j)
					st.Nodes[id] = Node{ID: id, Title: id, Status: "active", Revision: 1, Reason: "fixture", Actor: "operator"}
					i := itemFixture("i."+id, "Archive lesson about customer needs")
					i.Node = id
					i.Revision = 1
					i.Reason = "fixture"
					st.Items[i.ID] = i
					st.Edges[id] = Edge{ID: id, From: i.ID, To: "purpose", Relation: "concerns", Reason: "fixture"}
				}
				return nil
			}); e != nil {
				t.Fatal(e)
			}
			st, _ := s.Read()
			p := Assemble(st, Activation{ID: "benchmark", Pursuit: "purpose", Phase: "work", Started: s.Now(), Deadline: s.Now().Add(time.Minute)})
			if len(Marshal(p)) > st.Config.ContextBytes || p.Omitted == 0 {
				t.Fatal("unbounded context")
			}
			v, e := s.Call(context.Background(), "operator", "links", Marshal(map[string]any{"id": "purpose", "limit": 100}))
			if e != nil || len(Marshal(v)) > st.Config.ResponseBytes {
				t.Fatal("unbounded hub", e)
			}
			readStart := time.Now()
			offset, recovered := 0, 0
			for {
				value, err := s.Call(context.Background(), "operator", "history", Marshal(map[string]any{"id": claim.ID, "offset": offset, "limit": 100}))
				if err != nil || len(Marshal(value)) > st.Config.ResponseBytes {
					t.Fatal("unbounded/unavailable revision history", err)
				}
				page := value.(pageResult)
				for _, row := range page.Items {
					event, ok := row.(Event)
					if !ok {
						t.Fatal("ordinary archived revision not available", row)
					}
					for _, change := range event.Changes {
						if change.Field != "items" || change.Key != claim.ID {
							continue
						}
						var old Item
						if err := json.Unmarshal(change.Value, &old); err != nil {
							t.Fatal(err)
						}
						recovered++
						want := fmt.Sprintf("Revision %d: ", recovered) + strings.Repeat("歴史 é 🧭; ", 80)
						if old.Revision != recovered || old.Text != want || old.Sources[0] != claim.Sources[0] {
							t.Fatal("archive lost content, order or original source age", recovered)
						}
					}
				}
				if page.Next < 0 {
					break
				}
				if page.Next <= offset {
					t.Fatal("history pagination cannot terminate", offset, page.Next)
				}
				offset = page.Next
			}
			if recovered != revisions {
				t.Fatal("omitted history not retrievable", recovered)
			}
			after, err := s.Read()
			if err != nil || after.Seq != st.Seq {
				t.Fatal("read pagination mutated history", err)
			}
			journal, err := os.Stat(s.path("events.jsonl"))
			if err != nil {
				t.Fatal(err)
			}
			var m runtime.MemStats
			runtime.ReadMemStats(&m)
			t.Logf("nodes=%d revisions=%d journal_bytes=%d packet_bytes=%d archive_build=%s archive_read=%s elapsed=%s heap=%d", count, revisions, journal.Size(), len(Marshal(p)), archiveBuild, time.Since(readStart), time.Since(start), m.HeapAlloc)
		})
	}
}

func TestEnvironmentAndCodexRoute(t *testing.T) {
	t.Setenv("OPENAI_API_KEY", "synthetic-do-not-inherit")
	t.Setenv("UNRELATED_SECRET", "synthetic-private")
	env := strings.Join(SafeEnvironment(), "\n")
	if strings.Contains(env, "synthetic-") {
		t.Fatal("ambient secret inherited")
	}
	c := NewState("goal").Config
	args := strings.Join(CodexArgs(c, "/binary", "/instance", "act.test", "/schema", "/output"), " ")
	for _, want := range []string{`forced_login_method="chatgpt"`, `features.apps=false`, `--ignore-user-config`, `read-only`, `--model gpt-6-luna`, `model_reasoning_effort="max"`} {
		if !strings.Contains(args, want) {
			t.Fatal("missing route isolation", want)
		}
	}
	u := codexUsage([]byte("{\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":12,\"output_tokens\":3,\"cached_input_tokens\":4}}\n"))
	if u.Input != 12 || u.Basis != "subscription" {
		t.Fatal(u)
	}
}

func TestBrowserEnvironmentExplicitlyReachesChild(t *testing.T) {
	t.Setenv("XDG_CONFIG_HOME", "/ambient-must-not-leak")
	s := fixture(t)
	if err := Atomic(s.path("environment.json"), []byte(`{"XDG_CONFIG_HOME":"/tmp/ui-config","XDG_CACHE_HOME":"/tmp/ui-cache","NODE_PATH":"/opt/ui/node_modules"}`)); err != nil {
		t.Fatal(err)
	}
	env, err := s.Environment()
	if err != nil {
		t.Fatal(err)
	}
	out, _, err := RunCommand(context.Background(), []string{"sh", "-c", `printf '%s|%s|%s' "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$NODE_PATH"`}, s.Dir, env, nil, 1000, nil)
	if err != nil || string(out) != "/tmp/ui-config|/tmp/ui-cache|/opt/ui/node_modules" {
		t.Fatalf("explicit browser environment lost: %q %v", out, err)
	}
}

func TestExternalSandboxIsExplicitAndDurable(t *testing.T) {
	s := Open(t.TempDir())
	if err := s.Init("sandbox configuration qualification"); err != nil {
		t.Fatal(err)
	}
	if err := s.Update("operator", "config", "invalid external sandbox without workspace", func(st *State) error {
		st.Config.ExternalSandbox = true
		return nil
	}); err == nil {
		t.Fatal("external sandbox silently granted workspace")
	}
	if err := s.Update("operator", "config", "explicit isolated container", func(st *State) error {
		st.Config.Workspace = true
		st.Config.ExternalSandbox = true
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(s.Dir).Read()
	if err != nil {
		t.Fatal(err)
	}
	args := strings.Join(CodexArgs(reopened.Config, "/binary", "/instance", "act.test", "/schema", "/output"), " ")
	if !strings.Contains(args, "--sandbox danger-full-access") || !strings.Contains(args, `forced_login_method="chatgpt"`) {
		t.Fatal("explicit isolated route not preserved", args)
	}
	reopened.Config.ExternalSandbox = false
	args = strings.Join(CodexArgs(reopened.Config, "/binary", "/instance", "act.test", "/schema", "/output"), " ")
	if !strings.Contains(args, "--sandbox workspace-write") {
		t.Fatal("workspace default changed")
	}
}

func TestProgramCancellationDoesNotManufactureFailureAttention(t *testing.T) {
	for _, kind := range []string{"disabled", "supervisor", "freeze", "failure"} {
		t.Run(kind, func(t *testing.T) {
			s := fixture(t)
			cmd := []string{"sleep", "30"}
			if kind == "failure" {
				cmd = []string{"sh", "-c", "exit 7"}
			}
			if err := s.Update("operator", "config", "real program qualification", func(st *State) error {
				st.Config.Workspace = true
				st.Programs["worker"] = Program{ID: "worker", Command: cmd, Pursuit: "purpose", Enabled: true}
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan error, 1)
			go func() { done <- s.RunProgram(ctx, "worker") }()
			if kind != "failure" {
				limit := time.Now().Add(3 * time.Second)
				for {
					st, err := s.Read()
					if err != nil {
						t.Fatal(err)
					}
					if Alive(st.Programs["worker"].Process) {
						break
					}
					if time.Now().After(limit) {
						t.Fatal("worker never started")
					}
					time.Sleep(10 * time.Millisecond)
				}
				switch kind {
				case "disabled":
					if err := s.StopProgram("worker"); err != nil {
						t.Fatal(err)
					}
				case "supervisor":
					cancel()
				case "freeze":
					if err := s.Freeze("test shutdown"); err != nil {
						t.Fatal(err)
					}
				}
			}
			select {
			case err := <-done:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("worker did not settle")
			}
			st, err := Open(s.Dir).Read()
			if err != nil {
				t.Fatal(err)
			}
			want := "stopped"
			if kind == "supervisor" {
				want = "interrupted"
			}
			if kind == "failure" {
				want = "failed"
			}
			if st.Programs["worker"].Status != want {
				t.Fatal(st.Programs["worker"])
			}
			if kind == "failure" {
				if len(st.Wakes) != 1 || st.Programs["worker"].LastError == "" {
					t.Fatal("real failure was hidden")
				}
			} else if len(st.Wakes) != 0 || st.Programs["worker"].LastError != "" {
				t.Fatal("cancellation manufactured failure attention", st.Wakes, st.Programs["worker"])
			}
		})
	}
}
