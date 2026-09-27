package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func TestInstanceIsolationAndExpiredAuthority(t *testing.T) {
	a, b := fixture(t), fixture(t)
	if e := a.Mutate("operator", "private lesson", Batch{Items: []ItemChange{{Item: itemFixture("private", "Only instance A knows this")}}}); e != nil {
		t.Fatal(e)
	}
	if _, e := b.Call(context.Background(), "operator", "item", Marshal(map[string]any{"id": "private"})); e == nil {
		t.Fatal("cross-instance read")
	}
	running(t, a)
	act, _, e := a.Admit()
	if e != nil {
		t.Fatal(e)
	}
	if e = b.Mutate(act.ID, "wrong identity", Batch{}); e == nil {
		t.Fatal("cross-instance actor")
	}
	if e = a.Freeze("boundary"); e != nil {
		t.Fatal(e)
	}
	if e = a.Mutate(act.ID, "expired identity", Batch{}); e == nil {
		t.Fatal("expired actor mutated state")
	}
}
func TestCompleteRecordCorruptionRejected(t *testing.T) {
	s := fixture(t)
	b, e := os.ReadFile(s.path("events.jsonl"))
	if e != nil {
		t.Fatal(e)
	}
	b[10] ^= 1
	if e = os.WriteFile(s.path("events.jsonl"), b, 0600); e != nil {
		t.Fatal(e)
	}
	if _, e = s.Read(); e == nil {
		t.Fatal("corrupt complete event accepted")
	}
}
func TestNoMutationNoSuccessorAndRest(t *testing.T) {
	s := fixture(t)
	running(t, s)
	a, _, _ := s.Admit()
	complete(t, s, a, "wait")
	if _, _, e := s.Admit(); e == nil {
		t.Fatal("manufactured work")
	}
	st, _ := s.Read()
	if len(st.Items) != 4 || st.Items["purpose"].Status != "active" {
		t.Fatal("rest erased goal or created knowledge")
	}
}

func TestWaitingTimeBound(t *testing.T) {
	s := NewState("fairness")
	i := s.Items["purpose"]
	i.Attention = nil
	s.Items[i.ID] = i
	s.Config.Reconsideration = "p.00"
	for j := 0; j < 20; j++ {
		weight := .8 / 19
		if j == 0 {
			weight = .2
		}
		fund(&s, fmt.Sprintf("p.%02d", j), weight, "ready")
	}
	last := map[string]int{}
	for turn := 1; turn <= 1000; turn++ {
		p, _, e := selectPursuit(&s, time.Now())
		if e != nil {
			t.Fatal(e)
		}
		s.Starts++
		if turn-last[p.ID] > 70 {
			t.Fatal("starvation", p.ID)
		}
		last[p.ID] = turn
	}
	if len(last) != 20 {
		t.Fatal("missing service")
	}
}

func TestExactAcknowledgmentsKeepConcurrentEvidence(t *testing.T) {
	s := fixture(t)
	b := Batch{Consequences: []Consequence{{ID: "old", Summary: "old finding", Targets: []string{"purpose"}}}}
	if e := s.Mutate("operator", "old finding", b); e != nil {
		t.Fatal(e)
	}
	b = Batch{Consequences: []Consequence{{ID: "new", Summary: "new finding", Targets: []string{"purpose"}}}, Acknowledge: []Ack{{ID: "old", Pursuit: "purpose", Reason: "considered old evidence; no change warranted"}}}
	if e := s.Mutate("operator", "reconcile exact old finding", b); e != nil {
		t.Fatal(e)
	}
	st, _ := s.Read()
	if st.Consequences["new"].Acknowledgments["purpose"] != "" || st.Consequences["old"].Acknowledgments["purpose"] == "" {
		t.Fatal("lost exact evidence boundary")
	}
}
func TestFailedTimerWakeRetained(t *testing.T) {
	s := fixture(t)
	if e := s.Mutate("operator", "one-shot", Batch{Timers: []Timer{{ID: "one", Pursuit: "purpose", Due: s.Now(), Active: true, Reason: "review"}}}); e != nil {
		t.Fatal(e)
	}
	running(t, s)
	a, _, _ := s.Admit()
	_ = s.Finish(a.ID, Outcome{}, fmt.Errorf("interrupted"))
	now := s.Now().Add(2 * time.Minute)
	s.Now = func() time.Time { return now }
	b, _, e := s.Admit()
	if e != nil || b.RecoveryOf != a.ID {
		t.Fatal("lost one-shot recovery", e)
	}
}

func TestExtensionAndAuthorityConfig(t *testing.T) {
	s := NewState("capability attachment")
	s.Config.MCP = map[string]MCPServer{"browser": {Command: []string{"browser-mcp", "--local"}, Environment: []string{"COMPANY_TOKEN"}, Approval: "approve"}}
	if e := Validate(s); e != nil {
		t.Fatal(e)
	}
	args := strings.Join(CodexArgs(s.Config, "c3", "instance", "act", "schema", "out"), " ")
	if !strings.Contains(args, `mcp_servers.browser.command="browser-mcp"`) || !strings.Contains(args, `mcp_servers.concorde.default_tools_approval_mode="approve"`) {
		t.Fatal(args)
	}
	s.Config.MCP["concorde"] = s.Config.MCP["browser"]
	if Validate(s) == nil {
		t.Fatal("core MCP hijacked")
	}
}
func TestDuplicateObservationsAndPortfolioConflict(t *testing.T) {
	s := fixture(t)
	for j := 0; j < 10; j++ {
		if e := s.Notify("purpose", "same", "same observation"); e != nil {
			t.Fatal(e)
		}
	}
	st, _ := s.Read()
	if len(st.Wakes) != 1 {
		t.Fatal("duplicate wakes")
	}
	if e := s.Notify("purpose", "same", "different"); e == nil {
		t.Fatal("collision hidden")
	}
	seq := st.Seq
	i := st.Items["purpose"]
	_ = s.Mutate("operator", "concurrent", Batch{Items: []ItemChange{{Item: itemFixture("new-evidence", "A materially new observation")}}})
	if e := s.Mutate("operator", "stale", Batch{ExpectedSeq: &seq, Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}); e == nil {
		t.Fatal("stale allocation")
	}
}

func TestCodexMeasuredUsageNotClaimedBusinessSuccess(t *testing.T) {
	u := codexUsage([]byte(`{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}`))
	b, _ := json.Marshal(u)
	if strings.Contains(string(b), "cost") || u.Basis != "subscription" {
		t.Fatal(u)
	}
	if codexUsage(nil).Quality != "unavailable" {
		t.Fatal("missing telemetry converted to zero")
	}
}

func TestFocusedWorkingStateAndGlobalRevision(t *testing.T) {
	s := fixture(t)
	i := itemFixture("constraint", "Do not contact external customers")
	i.Kind = "norm"
	i.AppliesTo = []string{"purpose"}
	if e := s.Mutate("operator", "scope", Batch{Items: []ItemChange{{Item: i}}}); e != nil {
		t.Fatal(e)
	}
	running(t, s)
	a, p, e := s.Admit()
	if e != nil {
		t.Fatal(e)
	}
	found := false
	for _, x := range p.Items {
		found = found || x.Item.ID == "constraint"
	}
	if !found {
		t.Fatal("directive missing")
	}
	st, _ := s.Read()
	i = st.Items["memory-practice"]
	i.Text = "A local discovery changes general practice"
	if e = s.Mutate(a.ID, "global revision", Batch{Items: []ItemChange{{ExpectedRevision: i.Revision, Item: i}}}); e != nil {
		t.Fatal(e)
	}
}
