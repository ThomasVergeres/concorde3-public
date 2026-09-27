package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

func fixture(t *testing.T) *Store {
	t.Helper()
	s := Open(t.TempDir())
	if e := s.Init("Maintain a useful service with honest outcomes"); e != nil {
		t.Fatal(e)
	}
	now := time.Date(2026, 9, 7, 0, 0, 0, 0, time.UTC)
	s.Now = func() time.Time { return now }
	return s
}
func running(t *testing.T, s *Store) {
	t.Helper()
	if e := s.SetMode("running"); e != nil {
		t.Fatal(e)
	}
}
func complete(t *testing.T, s *Store, a Activation, decision string) {
	t.Helper()
	st, _ := s.Read()
	if st.Activations[a.ID].Phase == "work" {
		if e := s.BeginRectification(a.ID, "work finished", "test-session"); e != nil {
			t.Fatal(e)
		}
	}
	st, _ = s.Read()
	if e := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Continuation: decision, Coverage: "explicitly uncovered in fixture", Reason: "test continuation"}); e != nil {
		t.Fatal(e)
	}
	if e := s.Finish(a.ID, Outcome{Summary: "rectified"}, nil); e != nil {
		t.Fatal(e)
	}
}
func itemFixture(id, text string) Item {
	return Item{ID: id, Node: "undertaking", Kind: "belief", Text: text, Status: "active"}
}
func fund(st *State, id string, weight float64, status string) {
	i := itemFixture(id, "Desired condition "+id)
	i.Kind = "intention"
	i.Attention = &Attention{Weight: weight, EffortState: status}
	i.Revision = 1
	i.Reason = "fixture"
	i.Actor = "operator"
	st.Items[id] = i
}
func TestFoundationalLoop(t *testing.T) {
	s := fixture(t)
	initial, err := s.Read()
	if err != nil || initial.Config.Model != "gpt-6-luna" || initial.Config.Effort != "max" {
		t.Fatalf("new instances must default to Luna 6 max: %+v, %v", initial.Config, err)
	}
	running(t, s)
	a, p, e := s.Admit()
	if e != nil {
		t.Fatal(e)
	}
	if p.Identity == "" || a.ContextHash == "" {
		t.Fatal("missing provenance")
	}
	if e = s.Mutate(a.ID, "encounter", Batch{Items: []ItemChange{{Item: itemFixture("lesson", "Customer outcome differs from current checks")}}, Consequences: []Consequence{{ID: "finding", Summary: "Reconsider promise", Targets: []string{"purpose"}, References: []string{"lesson"}}}}); e != nil {
		t.Fatal(e)
	}
	complete(t, s, a, "continue")
	r := Open(s.Dir)
	r.Now = s.Now
	b, _, e := r.Admit()
	if e != nil {
		t.Fatal(e)
	}
	st, _ := r.Read()
	if st.Config.Model != "gpt-6-luna" || st.Config.Effort != "max" {
		t.Fatal("restart lost model defaults")
	}
	if st.Items["lesson"].Actor != a.ID || st.Consequences["finding"].ID == "" {
		t.Fatal("lost state")
	}
	complete(t, r, b, "wait")
	if _, _, e = r.Admit(); e == nil {
		t.Fatal("implicit perpetual readiness")
	}
}
func TestReplayAndPartialTail(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	if e := os.Remove(s.path("state.json")); e != nil {
		t.Fatal(e)
	}
	got, e := s.Read()
	if e != nil || got.Config.ID != st.Config.ID {
		t.Fatal(e)
	}
	f, _ := os.OpenFile(s.path("events.jsonl"), os.O_APPEND|os.O_WRONLY, 0600)
	_, _ = f.WriteString("{\"interrupted\":")
	f.Close()
	if _, e = s.Read(); e != nil {
		t.Fatal(e)
	}
	running(t, s)
	if _, _, e = s.Admit(); e != nil {
		t.Fatal(e)
	}
}
func TestConflictsAtomicAndEvidenceAge(t *testing.T) {
	s := fixture(t)
	n := itemFixture("lesson", "Original")
	n.Sources = []Source{{Ref: "artifacts/original", ObservedAt: s.Now().Add(-time.Hour)}}
	if e := s.Mutate("operator", "source", Batch{Items: []ItemChange{{Item: n}}}); e != nil {
		t.Fatal(e)
	}
	n.Text = "Revised"
	before, _ := s.Read()
	if e := s.Mutate("operator", "stale", Batch{Items: []ItemChange{{Item: itemFixture("must-not-survive", "Earlier change in rejected batch")}, {Item: n}}}); e == nil {
		t.Fatal("stale accepted")
	}
	after, err := Open(s.Dir).Read()
	if err != nil || string(Marshal(before)) != string(Marshal(after)) {
		t.Fatal("rejected batch changed durable state", err)
	}
	if e := s.Mutate("operator", "reconsider", Batch{Items: []ItemChange{{ExpectedRevision: 1, Item: n}}}); e != nil {
		t.Fatal(e)
	}
	st, _ := s.Read()
	if !st.Items[n.ID].Sources[0].ObservedAt.Equal(n.Sources[0].ObservedAt) {
		t.Fatal("age refreshed")
	}
	// A current revision is not a replacement for its original evidence.
	history, err := Open(s.Dir).History(n.ID, 0, 100)
	if err != nil {
		t.Fatal(err)
	}
	revisions := map[int]Item{}
	for _, ev := range history {
		for _, change := range ev.Changes {
			if change.Field == "items" && change.Key == n.ID && !change.Delete {
				var item Item
				if err := json.Unmarshal(change.Value, &item); err != nil {
					t.Fatal(err)
				}
				revisions[item.Revision] = item
			}
		}
	}
	if len(revisions) != 2 || revisions[1].Text != "Original" || revisions[2].Text != "Revised" {
		t.Fatal("original/revised understanding not retrievable", revisions)
	}
	for revision, item := range revisions {
		wantReason := "source"
		if revision == 2 {
			wantReason = "reconsider"
		}
		if item.Actor != "operator" || item.Reason != wantReason || len(item.Sources) != 1 || item.Sources[0] != n.Sources[0] {
			t.Fatal("lost revision provenance or source age", item)
		}
	}
}
func TestConcurrentAdmissionAndRestartBudget(t *testing.T) {
	s := fixture(t)
	running(t, s)
	var wg sync.WaitGroup
	var mu sync.Mutex
	n := 0
	for j := 0; j < 12; j++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := Open(s.Dir)
			r.Now = s.Now
			if _, _, e := r.Admit(); e == nil {
				mu.Lock()
				n++
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	if n != 1 {
		t.Fatal(n)
	}
	st, _ := s.Read()
	for _, a := range st.Activations {
		complete(t, s, a, "continue")
	}
	for j := 1; j < 6; j++ {
		a, _, e := s.Admit()
		if e != nil {
			t.Fatal(e)
		}
		complete(t, s, a, "continue")
	}
	r := Open(s.Dir)
	r.Now = s.Now
	if _, _, e := r.Admit(); e == nil {
		t.Fatal("lost budget")
	}
}
func TestFairnessAndSplitConservation(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	fund(&st, "purpose", .2, "ready")
	fund(&st, "a", .7, "ready")
	fund(&st, "b", .1, "ready")
	counts := map[string]int{}
	for j := 0; j < 100; j++ {
		p, _, e := selectPursuit(&st, s.Now())
		if e != nil {
			t.Fatal(e)
		}
		st.Starts++
		counts[p.ID]++
	}
	if counts["purpose"] < 19 || counts["b"] < 9 {
		t.Fatal(counts)
	}
	fund(&st, "mint", .5, "ready")
	if Validate(st) == nil {
		t.Fatal("minted attention")
	}
}
func TestTimerWakeAndExactConsequenceAck(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, e := s.Admit()
	if e != nil {
		t.Fatal(e)
	}
	if e = s.Notify("purpose", "new-evidence", "arrived during work"); e != nil {
		t.Fatal(e)
	}
	complete(t, s, a, "wait")
	b, p, e := s.Admit()
	if e != nil || b.ID == a.ID || len(p.Wakes) != 1 {
		t.Fatal("lost evidence", e)
	}
}
func TestContextBounds(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, p, e := s.Admit()
	if e != nil {
		t.Fatal(e)
	}
	b, _ := json.Marshal(p)
	if len(b) > a.Config.ContextBytes {
		t.Fatal("unbounded")
	}
	if _, e = os.Stat(filepath.Join(s.Dir, Runtime, "contexts", a.ID+".json")); e != nil {
		t.Fatal(e)
	}
}
func TestItemMoveSizeAndRetirement(t *testing.T) {
	s := fixture(t)
	i := itemFixture("finding", "One idea")
	if e := s.Mutate("operator", "new", Batch{Nodes: []NodeChange{{Node: Node{ID: "elsewhere", Title: "Elsewhere", Status: "active"}}}, Items: []ItemChange{{Item: i}}, Edges: []Edge{{ID: "link", From: "finding", To: "purpose", Relation: "concerns"}}}); e != nil {
		t.Fatal(e)
	}
	i.Node = "elsewhere"
	if e := s.Mutate("operator", "move", Batch{Items: []ItemChange{{ExpectedRevision: 1, Item: i}}}); e != nil {
		t.Fatal(e)
	}
	st, _ := s.Read()
	if st.Edges["link"].From != "finding" || st.Items["finding"].Node != "elsewhere" {
		t.Fatal("lost linkage")
	}
	i.Text = strings.Repeat("x", 5000)
	if e := s.Mutate("operator", "oversize", Batch{Items: []ItemChange{{ExpectedRevision: 2, Item: i}}}); e == nil {
		t.Fatal("oversize accepted")
	}
}
func TestPhaseLifecycleAndConcurrentCompletion(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	if e := s.CompletePhase(a.ID, Completion{Reason: "invalid"}); e == nil {
		t.Fatal("completed in work")
	}
	if e := s.BeginRectification(a.ID, "actual work", "thread"); e != nil {
		t.Fatal(e)
	}
	st, _ := s.Read()
	c := Completion{ExpectedSeq: st.Seq, Continuation: "wait", Reason: "adequate", Coverage: "uncovered"}
	_ = s.Notify("purpose", "late", "new event")
	if e := s.CompletePhase(a.ID, c); e == nil {
		t.Fatal("concurrent evidence hidden")
	}
	complete(t, s, a, "wait")
	st, _ = s.Read()
	if st.Items["purpose"].Status != "active" {
		t.Fatal("waiting erased desire")
	}
}
func TestKitsAndDomainContext(t *testing.T) {
	s := fixture(t)
	st, _ := s.Read()
	for _, bad := range []string{"understanding", "situation", "practice"} {
		if st.Nodes[bad].ID != "" {
			t.Fatal("old seed", bad)
		}
	}
	for _, tool := range Tools() {
		if strings.Contains(tool.Description, "body slice") || strings.Contains(tool.Description, "portfolio requires") {
			t.Fatal("stale schema", tool)
		}
	}
	i := itemFixture("engineering-norm", "Preserve original interfaces when refactoring")
	i.Kind = "norm"
	i.AppliesTo = []string{"engineering"}
	if e := s.Mutate("operator", "domain", Batch{Items: []ItemChange{{Item: i}}}); e != nil {
		t.Fatal(e)
	}
	running(t, s)
	a, _, _ := s.Admit()
	p, e := s.RefreshContext(context.Background(), a.ID, "engineering")
	if e != nil {
		t.Fatal(e)
	}
	found := false
	for _, x := range p.Items {
		found = found || x.Item.ID == i.ID
	}
	if !found {
		t.Fatal("missing domain norm")
	}
	st, _ = s.Read()
	kit := st.Items["rectification-practice"]
	kit.Text = "Revised practice marker"
	if e = s.Mutate(a.ID, "revise practice", Batch{Items: []ItemChange{{ExpectedRevision: kit.Revision, Item: kit}}}); e != nil {
		t.Fatal(e)
	}
	_ = s.BeginRectification(a.ID, "work", "")
	p, e = s.RefreshContext(context.Background(), a.ID, "")
	if e != nil {
		t.Fatal(e)
	}
	if !strings.Contains(string(Marshal(p)), "Revised practice marker") {
		t.Fatal("kit not live")
	}
}
func TestRecoveryBackoffAndNoBlindWork(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	_ = s.Finish(a.ID, Outcome{}, fmt.Errorf("failure"))
	if _, _, e := s.Admit(); e == nil {
		t.Fatal("no backoff")
	}
	now := s.Now().Add(2 * time.Minute)
	s.Now = func() time.Time { return now }
	b, _, e := s.Admit()
	if e != nil || b.Phase != "rectification" || b.RecoveryOf != a.ID {
		t.Fatal(b, e)
	}
	complete(t, s, b, "wait")
}

func TestRecoveryCarriesOriginalObservations(t *testing.T) {
	s := fixture(t)
	running(t, s)
	_ = s.Notify("purpose", "original", "important original evidence")
	a, _, _ := s.Admit()
	_ = s.Finish(a.ID, Outcome{}, fmt.Errorf("interrupted"))
	now := s.Now().Add(2 * time.Minute)
	s.Now = func() time.Time { return now }
	_, p, e := s.Admit()
	if e != nil {
		t.Fatal(e)
	}
	if len(p.Wakes) != 1 || p.Wakes[0].ID != "original" {
		t.Fatal("recovery lost original evidence")
	}
}
func TestPhaseContextExcludesInactiveKit(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, p, _ := s.Admit()
	for _, i := range p.Items {
		if i.Item.ID == "rectification-practice" {
			t.Fatal("inactive kit in work")
		}
	}
	_ = s.BeginRectification(a.ID, "work", "")
	p, e := s.RefreshContext(context.Background(), a.ID, "")
	if e != nil {
		t.Fatal(e)
	}
	for _, i := range p.Items {
		if i.Item.ID == "memory-practice" {
			t.Fatal("inactive kit in rectification")
		}
	}
}
func TestResumePreservesExplicitMCPConfig(t *testing.T) {
	args := resumeArgs(append([]string{"codex"}, CodexArgs(NewState("test").Config, "/binary", "/instance", "act.test", "/schema", "/out")...), "session-1")
	at := -1
	for j, x := range args {
		if x == "resume" {
			at = j
		}
	}
	if at < 0 {
		t.Fatal("missing resume")
	}
	for j, x := range args {
		if x == "--config" && j < at {
			t.Fatal("resume CLI scope would drop inherited MCP overrides")
		}
	}
	text := strings.Join(args, " ")
	if !strings.Contains(text, `mcp_servers.concorde.required=true`) || !strings.Contains(text, "session-1 -") {
		t.Fatal(args)
	}
}

func TestExplicitRefreshDoesNotLoopArtifactDeferral(t *testing.T) {
	s := fixture(t)
	_ = s.Update("operator", "config", "workspace fixture", func(st *State) error { st.Config.Workspace = true; return nil })
	running(t, s)
	a, _, _ := s.Admit()
	q := Marshal(map[string]any{"path": "result.json", "write": "{}"})
	v, e := s.Call(context.Background(), a.ID, "artifact", q)
	if e != nil || v.(map[string]any)["deferred"] != true {
		t.Fatal(v, e)
	}
	_, e = s.Call(context.Background(), a.ID, "context", Marshal(map[string]any{"query": "Confirm this authorized artifact retry"}))
	if e != nil {
		t.Fatal(e)
	}
	v, e = s.Call(context.Background(), a.ID, "artifact", q)
	if e != nil || v.(map[string]any)["written"] != true {
		t.Fatal("repeated deferral", v, e)
	}
	st, _ := s.Read()
	i := st.Items["memory-practice"]
	i.Text = "Materially revised instruction for this synthetic refresh test."
	if err := s.Mutate(a.ID, "new norm", Batch{Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}); err != nil {
		t.Fatal("instruction mutation must actually commit before checking stale context", err)
	}
	v, e = s.Call(context.Background(), a.ID, "artifact", q)
	if e != nil || v.(map[string]any)["deferred"] != true {
		t.Fatal("stale instructions bypassed", v, e)
	}
}

func TestDuplicateWakeDoesNotInvalidateRectification(t *testing.T) {
	s := fixture(t)
	_ = s.Notify("purpose", "same", "same evidence")
	before, _ := s.Read()
	for j := 0; j < 20; j++ {
		if e := s.Notify("purpose", "same", "same evidence"); e != nil {
			t.Fatal(e)
		}
	}
	after, _ := s.Read()
	if after.Seq != before.Seq {
		t.Fatal("duplicate evidence manufactured revision conflicts")
	}
}

func TestCommittedRectificationSurvivesFinalHarnessFailure(t *testing.T) {
	for _, recover := range []bool{false, true} {
		s := fixture(t)
		running(t, s)
		a, _, _ := s.Admit()
		_ = s.BeginRectification(a.ID, "work", "")
		st, _ := s.Read()
		if e := s.CompletePhase(a.ID, Completion{ExpectedSeq: st.Seq, Continuation: "wait", Coverage: "completed one-shot", Reason: "adequate"}); e != nil {
			t.Fatal(e)
		}
		if recover {
			_ = s.Recover()
		} else {
			_ = s.Finish(a.ID, Outcome{}, fmt.Errorf("final response interrupted"))
		}
		st, _ = s.Read()
		if st.Activations[a.ID].Phase != "completed" {
			t.Fatal("replayed committed rectification")
		}
		if _, _, e := s.Admit(); e == nil {
			t.Fatal("manufactured recovery attention")
		}
	}
}
