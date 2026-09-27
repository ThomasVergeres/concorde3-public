package core

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"regexp"
	"runtime/debug"
	"strings"
	"time"
)

const Version = 2
const Runtime = ".concorde2"
const Identity = `You are one persistent Concorde, temporarily attending through this activation. Your graph is fallible, revisable memory. Preserve useful discoveries, unfinished work, uncertainty and actual effects durably. Desires are not observations. You may reconsider any part of your own undertaking; locality is context, not authority. Honor material stakeholder instructions and actual authority boundaries. Do not invent a human management chain. Use judgment about private information and external effects; never claim an unobserved result. Durable state is changed through Concorde tools, not by editing brain.json or runtime files. Thought practices are revisable knowledge, not mandatory ceremonies.`

type Config struct {
	MCP               map[string]MCPServer `json:"mcp,omitempty"`
	ID                string               `json:"id"`
	Identity          string               `json:"identity"`
	StartsPerHour     int                  `json:"starts_per_hour"`
	Concurrency       int                  `json:"concurrency"`
	DeadlineSeconds   int                  `json:"deadline_seconds"`
	ContextBytes      int                  `json:"context_bytes"`
	ResponseBytes     int                  `json:"response_bytes"`
	BroadShare        float64              `json:"broad_share"`
	Reconsideration   string               `json:"reconsideration"`
	DeferralSeconds   int                  `json:"deferral_seconds,omitempty"`
	ReconsiderSeconds int                  `json:"reconsider_seconds,omitempty"`
	WorkReentry       bool                 `json:"work_reentry,omitempty"`
	Global            []string             `json:"global"`
	WorkPractice      string               `json:"work_practice"`
	RectifyPractice   string               `json:"rectify_practice"`
	FreezeAt          time.Time            `json:"freeze_at,omitempty"`
	Harness           string               `json:"harness"`
	Command           []string             `json:"command,omitempty"`
	Model             string               `json:"model"`
	Effort            string               `json:"effort"`
	Workspace         bool                 `json:"workspace"`
	ExternalSandbox   bool                 `json:"external_sandbox,omitempty"`
	EmbeddingCommand  []string             `json:"embedding_command,omitempty"`
}
type MCPServer struct {
	Command           []string `json:"command,omitempty"`
	URL               string   `json:"url,omitempty"`
	Environment       []string `json:"environment,omitempty"`
	BearerEnvironment string   `json:"bearer_environment,omitempty"`
	Approval          string   `json:"approval"`
}

var BuildStamp string

func BuildRevision() string {
	if BuildStamp != "" {
		return BuildStamp
	}
	if b, ok := debug.ReadBuildInfo(); ok {
		rev, dirty := "unversioned", false
		for _, s := range b.Settings {
			if s.Key == "vcs.revision" {
				rev = s.Value
			}
			if s.Key == "vcs.modified" {
				dirty = s.Value == "true"
			}
		}
		if dirty {
			rev += "+dirty"
		}
		return rev
	}
	return "unversioned"
}

type Source struct {
	Ref        string    `json:"ref"`
	ObservedAt time.Time `json:"observed_at,omitzero"`
}
type Node struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	Status    string    `json:"status"`
	Revision  int       `json:"revision"`
	UpdatedAt time.Time `json:"updated_at"`
	Actor     string    `json:"actor"`
	Reason    string    `json:"reason"`
}
type Attention struct {
	Weight      float64   `json:"weight"`
	EffortState string    `json:"effort_state"`
	NextAt      time.Time `json:"next_at,omitempty"`
	DeferredAt  time.Time `json:"deferred_at,omitempty"`
}
type Item struct {
	ID        string     `json:"id"`
	Node      string     `json:"node"`
	Kind      string     `json:"kind"`
	Text      string     `json:"text"`
	Status    string     `json:"status"`
	Approach  string     `json:"approach,omitempty"`
	Sources   []Source   `json:"sources,omitempty"`
	AppliesTo []string   `json:"applies_to,omitempty"`
	Attention *Attention `json:"attention,omitempty"`
	Revision  int        `json:"revision"`
	UpdatedAt time.Time  `json:"updated_at"`
	Actor     string     `json:"actor"`
	Reason    string     `json:"reason"`
}
type Edge struct {
	ID       string `json:"id"`
	From     string `json:"from"`
	To       string `json:"to"`
	Relation string `json:"relation"`
	Reason   string `json:"reason"`
}
type Pursuit struct {
	ID           string    `json:"id"`
	Node         string    `json:"node"`
	Share        float64   `json:"share"`
	Status       string    `json:"status"`
	NextAt       time.Time `json:"next_at,omitempty"`
	ReconsiderAt time.Time `json:"reconsider_at,omitempty"`
}

// Pursuit is a computed scheduling view, never separately persisted memory.
func (s State) Portfolio() map[string]Pursuit {
	out := map[string]Pursuit{}
	for id, i := range s.Items {
		if i.Attention != nil {
			a := i.Attention
			out[id] = Pursuit{ID: id, Node: i.Node, Share: a.Weight, Status: a.EffortState, NextAt: a.NextAt}
			p := out[id]
			p.ReconsiderAt = reconsiderAt(s, p)
			out[id] = p
		}
	}
	return out
}

type Consequence struct {
	ID              string            `json:"id"`
	Summary         string            `json:"summary"`
	References      []string          `json:"references"`
	Targets         []string          `json:"targets"`
	CreatedAt       time.Time         `json:"created_at"`
	Actor           string            `json:"actor"`
	Acknowledgments map[string]string `json:"acknowledgments,omitempty"`
}
type Timer struct {
	ID              string    `json:"id"`
	Pursuit         string    `json:"intention"`
	Due             time.Time `json:"due"`
	IntervalSeconds int       `json:"interval_seconds"`
	Active          bool      `json:"active"`
	Reason          string    `json:"reason"`
}
type Wake struct {
	ID         string    `json:"id"`
	Pursuit    string    `json:"intention"`
	Evidence   string    `json:"evidence"`
	At         time.Time `json:"at"`
	ConsumedBy string    `json:"consumed_by,omitempty"`
}
type Process struct {
	PID   int    `json:"pid"`
	Start string `json:"start"`
}
type Usage struct {
	Quality string `json:"quality"`
	Basis   string `json:"basis"`
	Input   int64  `json:"input,omitempty"`
	Output  int64  `json:"output,omitempty"`
	Cached  int64  `json:"cached,omitempty"`
}
type Activation struct {
	CodeRevision     string      `json:"code_revision"`
	ID               string      `json:"id"`
	Pursuit          string      `json:"intention"`
	Phase            string      `json:"phase"`
	Session          string      `json:"session,omitempty"`
	Activity         string      `json:"activity,omitempty"`
	PreparedContext  string      `json:"prepared_context,omitempty"`
	PreparedContexts []string    `json:"prepared_contexts,omitempty"`
	RecoveryOf       string      `json:"recovery_of,omitempty"`
	RecoveryAttempts int         `json:"recovery_attempts,omitempty"`
	RetryAt          time.Time   `json:"retry_at,omitempty"`
	WorkSummary      string      `json:"work_summary,omitempty"`
	WorkReturnReason string      `json:"work_return_reason,omitempty"`
	WorkReturns      int         `json:"work_returns,omitempty"`
	Completion       *Completion `json:"completion,omitempty"`
	Started          time.Time   `json:"started"`
	Deadline         time.Time   `json:"deadline"`
	Finished         time.Time   `json:"finished,omitempty"`
	Status           string      `json:"status"`
	Reason           string      `json:"reason"`
	Share            float64     `json:"share"`
	ContextHash      string      `json:"context_hash"`
	Config           Config      `json:"config"`
	Process          Process     `json:"process"`
	Summary          string      `json:"summary,omitempty"`
	Usage            Usage       `json:"usage"`
}
type Program struct {
	ID              string     `json:"id"`
	Command         []string   `json:"command"`
	Pursuit         string     `json:"intention"`
	Enabled         bool       `json:"enabled"`
	IntervalSeconds int        `json:"interval_seconds"`
	NextAt          time.Time  `json:"next_at,omitempty"`
	Process         Process    `json:"process"`
	Status          string     `json:"status"`
	LastAt          time.Time  `json:"last_at,omitempty"`
	LastError       string     `json:"last_error,omitempty"`
	LastStderr      string     `json:"last_stderr,omitempty"`
	Watch           *WatchSpec `json:"watch,omitempty"`
	Observed        WatchState `json:"observed,omitempty"`
}
type Receipt struct {
	Key         string    `json:"key"`
	Fingerprint string    `json:"fingerprint"`
	Status      string    `json:"status"`
	Actor       string    `json:"actor"`
	At          time.Time `json:"at"`
	HTTPStatus  int       `json:"http_status,omitempty"`
	Detail      string    `json:"detail,omitempty"`
}
type State struct {
	Version      int                    `json:"version"`
	Seq          int64                  `json:"seq"`
	Config       Config                 `json:"config"`
	Mode         string                 `json:"mode"`
	Nodes        map[string]Node        `json:"nodes"`
	Edges        map[string]Edge        `json:"edges"`
	Items        map[string]Item        `json:"items"`
	Consequences map[string]Consequence `json:"consequences"`
	Timers       map[string]Timer       `json:"timers"`
	Wakes        map[string]Wake        `json:"wakes"`
	Activations  map[string]Activation  `json:"activations"`
	Programs     map[string]Program     `json:"programs"`
	Receipts     map[string]Receipt     `json:"receipts"`
	Credits      map[string]float64     `json:"credits"`
	Waits        map[string]int         `json:"waits"`
	Starts       int64                  `json:"starts"`
	LastUrgent   int64                  `json:"last_urgent"`
}

func NewState(goal string) State {
	now := time.Now().UTC()
	s := State{Version: Version, Mode: "paused", Config: Config{ID: NewID("concorde"), Identity: Identity, StartsPerHour: 6, Concurrency: 1, DeadlineSeconds: 600, ContextBytes: 24000, ResponseBytes: 16000, BroadShare: .2, Reconsideration: "purpose", Global: []string{"purpose", "capabilities"}, WorkPractice: "memory-practice", RectifyPractice: "rectification-practice", Harness: "codex", Command: []string{"codex"}, Model: "gpt-6-luna", Effort: "max"}, Nodes: map[string]Node{}, Items: map[string]Item{}, Edges: map[string]Edge{}, Consequences: map[string]Consequence{}, Timers: map[string]Timer{}, Wakes: map[string]Wake{}, Activations: map[string]Activation{}, Programs: map[string]Program{}, Receipts: map[string]Receipt{}, Credits: map[string]float64{}}
	for _, n := range []Node{{ID: "undertaking", Title: "Undertaking"}, {ID: "memory-kit", Title: "Working practice"}, {ID: "rectification-kit", Title: "Rectification practice"}, {ID: "tools", Title: "Available capabilities"}} {
		n.Status = "active"
		n.Revision = 1
		n.Actor = "operator"
		n.Reason = "initial seed"
		n.UpdatedAt = now
		s.Nodes[n.ID] = n
	}
	for _, i := range []Item{
		{ID: "purpose", Node: "undertaking", Kind: "intention", Text: goal, Attention: &Attention{Weight: 1, EffortState: "ready"}},
		{ID: "memory-practice", Node: "memory-kit", Kind: "norm", Text: MemoryPractice},
		{ID: "rectification-practice", Node: "rectification-kit", Kind: "norm", Text: RectificationPractice},
		{ID: "capabilities", Node: "tools", Kind: "capability", Text: CapabilityDescription},
	} {
		i.Status = "active"
		i.Revision = 1
		i.Actor = "operator"
		i.Reason = "initial seed"
		i.UpdatedAt = now
		s.Items[i.ID] = i
	}
	s.Waits = map[string]int{}
	return s
}
func NewID(prefix string) string {
	b := make([]byte, 12)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return prefix + "." + hex.EncodeToString(b)
}

var identifier = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$`)

func validID(s string) bool { return identifier.MatchString(s) }
func clone[T any](v T) T    { b, _ := json.Marshal(v); var x T; _ = json.Unmarshal(b, &x); return x }
func Validate(s State) error {
	c := s.Config
	if c.ExternalSandbox && !c.Workspace {
		return fmt.Errorf("external_sandbox requires explicit workspace permission and operator-provided OS isolation")
	}
	if c.DeferralSeconds < 0 || c.ReconsiderSeconds < 0 || c.DeferralSeconds > 31536000 || c.ReconsiderSeconds > 31536000 {
		return fmt.Errorf("deferral_seconds/reconsider_seconds must be 0 (default) or 1..31536000")
	}
	if s.Version != Version || !validID(c.ID) || c.Identity == "" || c.StartsPerHour < 1 || c.Concurrency < 1 || c.DeadlineSeconds < 1 || c.ContextBytes < 4096 || c.ResponseBytes < 1024 || c.BroadShare <= 0 || c.BroadShare > 1 {
		return fmt.Errorf("invalid config/version")
	}
	if s.Mode != "paused" && s.Mode != "running" && s.Mode != "frozen" {
		return fmt.Errorf("invalid mode")
	}
	for id, m := range c.MCP {
		if !identifier.MatchString(id) || id == "concorde" || strings.ContainsAny(id, ".:") {
			return fmt.Errorf("MCP name must be a simple identifier, not concorde")
		}
		if (len(m.Command) == 0) == (m.URL == "") {
			return fmt.Errorf("MCP %s requires exactly one command or URL", id)
		}
		if m.Approval != "approve" && m.Approval != "auto" && m.Approval != "prompt" && m.Approval != "writes" {
			return fmt.Errorf("explicit MCP approval mode required")
		}
	}
	sum, broad := 0.0, 0.0
	for id, n := range s.Nodes {
		if id != n.ID || !validID(id) || n.Revision < 1 || n.Title == "" || n.Reason == "" {
			return fmt.Errorf("invalid node %s", id)
		}
	}
	for id, i := range s.Items {
		if id != i.ID || !validID(id) || s.Nodes[id].ID != "" || s.Nodes[i.Node].ID == "" || i.Text == "" || i.Kind == "" || i.Revision < 1 || i.Reason == "" {
			return fmt.Errorf("invalid item %s", id)
		}
		if i.Attention != nil && (i.Kind != "intention" || i.Status == "retired") {
			return fmt.Errorf("only live intention items can carry attention: %s", id)
		}
	}
	views := map[string]NodeView{}
	for id, n := range s.Nodes {
		views[id] = NodeView{Node: n, Items: []Item{}}
	}
	for _, i := range s.Items {
		if i.Status != "retired" {
			v := views[i.Node]
			v.Items = append(v.Items, i)
			views[i.Node] = v
		}
	}
	for _, v := range views {
		b, _ := json.Marshal(v)
		if len(b) >= 5000 {
			return nodeBoundError(v, len(b))
		}
	}
	for _, id := range append(append([]string{}, c.Global...), c.WorkPractice, c.RectifyPractice) {
		if s.Items[id].ID == "" || s.Items[id].Status == "retired" {
			return fmt.Errorf("missing live context binding %s", id)
		}
	}
	for id, e := range s.Edges {
		if id != e.ID || !validID(id) || !s.HasRef(e.From) || !s.HasRef(e.To) || e.Relation == "" || e.Reason == "" {
			return fmt.Errorf("invalid edge %s", id)
		}
	}
	for id, p := range s.Portfolio() {
		if id != p.ID || !validID(id) || s.Nodes[p.Node].ID == "" || math.IsNaN(p.Share) || math.IsInf(p.Share, 0) || p.Share < 0 || p.Share > 1 {
			return fmt.Errorf("invalid intention attention %s", id)
		}
		if p.Status != "ready" && p.Status != "waiting" && p.Status != "dormant" && p.Status != "stopped" {
			return fmt.Errorf("invalid effort_state for intention %s", id)
		}
		// Older v2 snapshots could contain an ignored next_at on stopped work.
		// Keep them readable; new contradictory requests fail at mutation/completion.
		if p.Status == "dormant" && !p.NextAt.IsZero() {
			return fmt.Errorf("dormant intention %s cannot have next_at; use waiting for dated return", id)
		}
		sum += p.Share
		if p.ID == c.Reconsideration {
			broad += p.Share
		}
	}
	if sum > 1.00000001 {
		return fmt.Errorf("portfolio shares exceed one; splitting does not mint capacity")
	}
	if sum > 0 && broad+1e-8 < c.BroadShare*sum {
		return fmt.Errorf("portfolio must preserve broad share %.3f", c.BroadShare)
	}
	for id, t := range s.Timers {
		if id != t.ID || !validID(id) || s.Portfolio()[t.Pursuit].ID == "" || t.IntervalSeconds < 0 || t.Due.IsZero() || t.Reason == "" {
			return fmt.Errorf("invalid timer %s", id)
		}
		if t.Active && s.Portfolio()[t.Pursuit].Status == "stopped" {
			return fmt.Errorf("cancel/rebind timer %s before ending pursuit", id)
		}
	}
	for id, p := range s.Programs {
		if id != p.ID || !validID(id) || len(p.Command) == 0 || p.IntervalSeconds < 0 || s.Portfolio()[p.Pursuit].ID == "" {
			return fmt.Errorf("invalid program %s", id)
		}
		if p.Watch != nil {
			if err := p.Watch.Validate(); err != nil {
				return err
			}
		}
	}
	for id, a := range s.Activations {
		if a.WorkReturns < 0 || a.WorkReturns > 1 || len(a.WorkReturnReason) > 1000 {
			return fmt.Errorf("invalid bounded work return for activation %s", id)
		}
	}
	for _, c := range s.Consequences {
		for _, id := range c.Targets {
			if c.Acknowledgments[id] == "" && (s.Items[id].Kind != "intention" || s.Items[id].Status == "retired") {
				return fmt.Errorf("pending consequence %s requires live intention %s", c.ID, id)
			}
		}
	}
	return nil
}
