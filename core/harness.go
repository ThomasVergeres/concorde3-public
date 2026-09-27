package core

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func CodexArgs(c Config, executable, dir, id, schemaPath, outputPath string) []string {
	args := []string{"--config", `forced_login_method="chatgpt"`, "--config", `model_provider="openai"`, "--config", `features.apps=false`, "--config", `features.skills=false`, "--config", `features.multi_agent=false`, "--config", fmt.Sprintf("model_reasoning_effort=%q", c.Effort), "--config", fmt.Sprintf("mcp_servers.concorde.command=%q", executable), "--config", "mcp_servers.concorde.args=" + string(Marshal([]string{"mcp", dir, id})), "--ask-for-approval", "never", "exec", "--ignore-user-config", "--ignore-rules", "--json", "--skip-git-repo-check", "--model", c.Model, "--sandbox", "read-only", "--output-schema", schemaPath, "--output-last-message", outputPath, "-"}
	args = append([]string{"--config", `mcp_servers.concorde.default_tools_approval_mode="approve"`, "--config", `mcp_servers.concorde.required=true`, "--config", `memories.generate_memories=false`, "--config", `memories.use_memories=false`}, args...)
	names := []string{}
	for name := range c.MCP {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		m := c.MCP[name]
		prefix := "mcp_servers." + name
		extra := []string{"--config", prefix + ".default_tools_approval_mode=" + fmt.Sprintf("%q", m.Approval)}
		if len(m.Command) > 0 {
			extra = append(extra, "--config", prefix+".command="+fmt.Sprintf("%q", m.Command[0]), "--config", prefix+".args="+string(Marshal(m.Command[1:])), "--config", prefix+".env_vars="+string(Marshal(m.Environment)))
		} else {
			extra = append(extra, "--config", prefix+".url="+fmt.Sprintf("%q", m.URL))
			if m.BearerEnvironment != "" {
				extra = append(extra, "--config", prefix+".bearer_token_env_var="+fmt.Sprintf("%q", m.BearerEnvironment))
			}
		}
		args = append(extra, args...)
	}
	// Shell workspace access is explicit; MCP canonical writes retain their own contracts.
	if c.Workspace {
		for i := range args {
			if args[i] == "read-only" {
				args[i] = "workspace-write"
				if c.ExternalSandbox {
					// Explicit operator assertion: a container/VM provides isolation.
					// Never infer this from a failed inner sandbox or auto-fallback.
					args[i] = "danger-full-access"
				}
			}
		}
	}
	return args
}
func (s *Store) Harness(ctx context.Context, a Activation, p Packet) (Outcome, error) {
	cfg := a.Config
	env, e := s.Environment()
	if e != nil {
		return Outcome{}, e
	}
	env = append(env, "CONCORDE3_ACTIVATION="+a.ID)
	if cfg.Harness == "command" {
		capctx, cancel := context.WithTimeout(ctx, 5*time.Second)
		b, _, err := RunCommand(capctx, append(append([]string{}, cfg.Command...), "--concorde-capabilities"), s.Dir, env, nil, 4000, nil)
		contextErr := capctx.Err()
		cancel()
		if contextErr != nil {
			return Outcome{}, fmt.Errorf("command harness capability preflight: %w", contextErr)
		}
		if err != nil {
			return Outcome{}, fmt.Errorf("command harness capability preflight failed: %w", err)
		}
		var caps struct {
			Protocol     int  `json:"protocol"`
			Continuation bool `json:"continuation"`
		}
		if Decode(b, &caps) != nil || caps.Protocol != 2 || !caps.Continuation {
			return Outcome{}, fmt.Errorf("command harness must advertise protocol 2 and continuation via --concorde-capabilities")
		}
		env = append(env, "CONCORDE3_PHASE="+a.Phase)
		b, stderr, e := RunCommand(ctx, cfg.Command, s.Dir, env, Marshal(p), 1<<20, func(ref Process) error { return s.AttachProcess(a.ID, ref) })
		if e != nil {
			return Outcome{}, fmt.Errorf("command harness: %w; %s", e, stderr)
		}
		var out Outcome
		if e = Decode(b, &out); e != nil {
			return out, e
		}
		// The selected adapter owns telemetry, not the model's prose. Preserve
		// explicit measurements/floors; legacy adapters without usage remain unknown.
		if out.Usage == (Usage{}) {
			out.Usage = Usage{Quality: "unavailable", Basis: "command"}
		} else if (out.Usage.Quality != "measured" && out.Usage.Quality != "partial" && out.Usage.Quality != "unavailable") ||
			out.Usage.Basis == "" || out.Usage.Input < 0 || out.Usage.Output < 0 || out.Usage.Cached < 0 || out.Usage.Cached > out.Usage.Input ||
			(out.Usage.Quality == "unavailable" && (out.Usage.Input != 0 || out.Usage.Output != 0 || out.Usage.Cached != 0)) {
			return Outcome{}, fmt.Errorf("command harness returned invalid usage telemetry")
		}
		return out, nil
	}
	if cfg.Harness != "codex" {
		return Outcome{}, fmt.Errorf("unknown harness %s", cfg.Harness)
	}
	// Neither ambient nor instance-provided API routes may silently replace subscription auth.
	clean := []string{}
	for _, v := range env {
		k, _, _ := strings.Cut(v, "=")
		if k == "OPENAI_API_KEY" || k == "CODEX_API_KEY" || k == "AZURE_OPENAI_API_KEY" || k == "OPENAI_BASE_URL" || k == "OPENAI_API_BASE" || k == "CODEX_BASE_URL" {
			continue
		}
		clean = append(clean, v)
	}
	env = clean
	loginCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
	command := cfg.Command
	if len(command) == 0 {
		command = []string{"codex"}
	}
	b, stderr, e := RunCommand(loginCtx, append(append([]string{}, command...), "login", "status"), s.Dir, env, nil, 4000, nil)
	contextErr := loginCtx.Err()
	cancel()
	if contextErr != nil {
		return Outcome{}, fmt.Errorf("Codex subscription preflight: %w", contextErr)
	}
	if e != nil {
		// A failed executable is not evidence of missing credentials. Never
		// echo login output here: it may contain private account material.
		return Outcome{}, fmt.Errorf("Codex subscription preflight failed: %w", e)
	}
	if !strings.Contains(string(b)+string(stderr), "Logged in using ChatGPT") {
		return Outcome{}, fmt.Errorf("Codex requires subscription login; run codex login with the intended account")
	}
	temp, e := os.MkdirTemp(s.path(""), "harness-")
	if e != nil {
		return Outcome{}, e
	}
	defer os.RemoveAll(temp)
	schema := filepath.Join(temp, "outcome.schema.json")
	output := filepath.Join(temp, "outcome.json")
	if e = Atomic(schema, []byte(`{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"],"additionalProperties":false}`)); e != nil {
		return Outcome{}, e
	}
	exe, e := os.Executable()
	if e != nil {
		return Outcome{}, e
	}
	args := append(append([]string{}, command...), CodexArgs(cfg, exe, s.Dir, a.ID, schema, output)...)
	if a.Session != "" {
		args = resumeArgs(args, a.Session)
	}
	prompt := p.Identity + "\n\nYou have Concorde MCP tools with input schemas; consult contract for further definitions/examples when needed. Persist useful understanding and unfinished work with mutate. Never modify brain.json or .concorde2 directly. At the end return only a JSON object with summary. Tool commits are durable. No hidden earlier activation history is part of this self. Current phase: " + a.Phase + ".\n\n" + string(Marshal(p))
	if a.Phase == "rectification" {
		prompt += "\nThis is the distinct rectification turn. Work is over. Use MCP reads/mutations and phase_complete. Read-only source inspection, including shell reads, is allowed; do not edit products, run new product workloads or perform new external effects. Reconcile concurrent/new evidence. Finish phase_complete before returning. Prior work summary: " + a.WorkSummary
		if a.Session == "" {
			prompt += "\nConversation unavailable: reconstruct only from durable graph, receipts, activation history and logs. This is recovery, not proof earlier work completed."
		}
	}
	if a.Config.WorkReentry && a.Phase == "rectification" && a.WorkReturns == 0 && a.RecoveryOf == "" {
		prompt += "\nOptional capability: before the original work-time boundary, resume_work may request one separate return to work in this activation. This turn remains state-only. After requesting, end with a summary without phase_complete; a new work turn and final rectification follow within the same deadline. No return is required."
	}
	if a.Phase == "work" && a.WorkReturns > 0 {
		prompt += "\nThis is the requested return to work, not rectification. Act within existing authority and remaining original work time. Another distinct final rectification turn follows. Your request: " + a.WorkReturnReason
	}
	b, stderr, e = RunCommand(ctx, args, s.Dir, env, []byte(prompt), 8<<20, func(ref Process) error { return s.AttachProcess(a.ID, ref) })
	_ = os.MkdirAll(s.path("harness-logs"), 0700)
	_ = Atomic(s.path("harness-logs/"+a.ID+"."+turnName(a)+".jsonl"), b)
	_ = Atomic(s.path("harness-logs/"+a.ID+"."+turnName(a)+".stderr"), stderr)
	out := Outcome{Usage: codexUsage(b), Session: sessionID(b)}
	if e != nil {
		return out, fmt.Errorf("Codex harness: %w; %s", e, stderr)
	}
	result, e := os.ReadFile(output)
	if e != nil {
		return out, e
	}
	if e = Decode(result, &out); e != nil {
		return out, e
	}
	out.Usage = codexUsage(b)
	out.Session = sessionID(b)
	return out, nil
}
func sessionID(b []byte) string {
	for _, line := range strings.Split(string(b), "\n") {
		var e struct {
			Type string `json:"type"`
			ID   string `json:"thread_id"`
		}
		if json.Unmarshal([]byte(line), &e) == nil && e.Type == "thread.started" {
			return e.ID
		}
	}
	return ""
}
func resumeArgs(args []string, id string) []string {
	out := []string{}
	configs := []string{}
	for i := 0; i < len(args); i++ {
		if args[i] == "--config" && i+1 < len(args) {
			configs = append(configs, "--config", args[i+1])
			i++
			continue
		}
		if args[i] == "--sandbox" && i+1 < len(args) {
			configs = append(configs, "--config", fmt.Sprintf("sandbox_mode=%q", args[i+1]))
			i++
			continue
		}
		if args[i] == "exec" {
			out = append(out, "exec", "resume")
			continue
		}
		if i == len(args)-1 && args[i] == "-" {
			out = append(out, configs...)
			out = append(out, id, "-")
			continue
		}
		out = append(out, args[i])
	}
	return out
}
func codexUsage(b []byte) Usage {
	u := Usage{Quality: "unavailable", Basis: "subscription"}
	for _, line := range strings.Split(string(b), "\n") {
		var ev struct {
			Type  string `json:"type"`
			Usage *struct {
				Input  int64 `json:"input_tokens"`
				Output int64 `json:"output_tokens"`
				Cached int64 `json:"cached_input_tokens"`
			} `json:"usage"`
		}
		if json.Unmarshal([]byte(line), &ev) == nil && ev.Type == "turn.completed" && ev.Usage != nil {
			u.Quality = "measured"
			u.Input += ev.Usage.Input
			u.Output += ev.Usage.Output
			u.Cached += ev.Usage.Cached
		}
	}
	return u
}
func turnName(a Activation) string {
	if a.WorkReturns > 0 {
		return fmt.Sprintf("%s.return%d", a.Phase, a.WorkReturns)
	}
	return a.Phase
}

func (s *Store) Execute(ctx context.Context, a Activation, p Packet) error {
	c, cancel := context.WithDeadline(ctx, a.Deadline)
	defer cancel()
	p, e := s.RefreshContext(c, a.ID, "")
	if e != nil {
		return s.Finish(a.ID, Outcome{}, e)
	}
	_ = Atomic(s.path("contexts/"+a.ID+"."+turnName(a)+".json"), Marshal(p))
	total := Usage{Quality: "measured", Basis: "subscription"}
	if a.Config.Harness == "command" {
		total.Basis = "command"
	}
	addUsage := func(u Usage) {
		if u.Quality != "measured" {
			total.Quality = "partial"
		}
		if u.Basis != "" {
			total.Basis = u.Basis
		}
		total.Input += u.Input
		total.Output += u.Output
		total.Cached += u.Cached
	}
	var out Outcome
	for {
		if a.Phase == "work" {
			workEnd := workBoundary(a)
			p.Deadline = workEnd
			_ = Atomic(s.path("contexts/"+a.ID+"."+turnName(a)+".json"), Marshal(p))
			wctx, wcancel := context.WithDeadline(c, workEnd)
			out, e = s.Harness(wctx, a, p)
			wcancel()
			addUsage(out.Usage)
			if e == nil && len(Marshal(out.Changes)) > 2 {
				_, e = s.Call(c, a.ID, "mutate", Marshal(map[string]any{"reason": "work writeback: " + out.Summary, "changes": out.Changes}))
			}
			summary := out.Summary
			if e != nil {
				summary = "Work interrupted/failed; inspect committed evidence: " + e.Error()
			}
			if err := s.BeginRectification(a.ID, summary, out.Session); err != nil {
				return s.Finish(a.ID, Outcome{Usage: total}, err)
			}
			st, err := s.Read()
			if err != nil {
				return err
			}
			a = st.Activations[a.ID]
		}
		p, e = s.RefreshContext(c, a.ID, "")
		if e != nil {
			return s.Finish(a.ID, Outcome{Usage: total}, e)
		}
		_ = Atomic(s.path("contexts/"+a.ID+"."+turnName(a)+".json"), Marshal(p))
		out, e = s.Harness(c, a, p)
		addUsage(out.Usage)
		if e == nil && len(Marshal(out.Changes)) > 2 {
			e = fmt.Errorf("rectification changes must be committed through MCP before phase_complete")
		}
		if e == nil && out.Completion != nil {
			_, e = s.Call(c, a.ID, "phase_complete", Marshal(map[string]any{"completion": out.Completion}))
		}
		if e == nil {
			st, err := s.Read()
			if err != nil {
				e = err
			} else {
				x := st.Activations[a.ID]
				if x.Completion == nil && x.WorkReturnReason != "" && x.WorkReturns == 0 {
					e = s.beginReturnedWork(a.ID, out.Session)
					if e == nil {
						st, e = s.Read()
						if e == nil {
							a = st.Activations[a.ID]
							p, e = s.RefreshContext(c, a.ID, "")
							if e == nil {
								continue
							}
						}
					}
				}
			}
		}
		break
	}
	out.Usage = total
	if c.Err() != nil && e == nil {
		e = c.Err()
	}
	if e == nil {
		st, err := s.Read()
		if err != nil {
			e = err
		} else if st.Activations[a.ID].Completion == nil {
			e = fmt.Errorf("rectification incomplete: phase_complete required")
		}
	}
	if e != nil {
		_ = os.MkdirAll(s.path("rejected"), 0700)
		_ = Atomic(s.path("rejected/"+a.ID+".json"), Marshal(map[string]any{"outcome": out, "error": e.Error()}))
	}
	finishErr := s.Finish(a.ID, out, e)
	if finishErr != nil {
		return finishErr
	}
	return e
}
