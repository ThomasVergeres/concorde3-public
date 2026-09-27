package core

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type boundedBuffer struct {
	mu        sync.Mutex
	b         bytes.Buffer
	max       int
	truncated bool
}

func (b *boundedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	n := len(p)
	left := b.max - b.b.Len()
	if len(p) > left {
		b.truncated = true
		p = p[:max(0, left)]
	}
	_, _ = b.b.Write(p)
	return n, nil
}
func SafeEnvironment() []string {
	out := []string{}
	for _, k := range []string{"PATH", "HOME", "CODEX_HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"} {
		if v, ok := os.LookupEnv(k); ok {
			out = append(out, k+"="+v)
		}
	}
	return out
}
func (s *Store) InstanceEnvironment() (map[string]string, error) {
	out := map[string]string{}
	b, e := os.ReadFile(s.path("environment.json"))
	if os.IsNotExist(e) {
		return out, nil
	}
	if e != nil {
		return nil, e
	}
	e = Decode(b, &out)
	return out, e
}
func (s *Store) Environment() ([]string, error) {
	env := SafeEnvironment()
	owned, e := s.InstanceEnvironment()
	if e != nil {
		return nil, e
	}
	for k, v := range owned {
		if k == "HOME" || k == "CODEX_HOME" || k == "PATH" {
			return nil, fmt.Errorf("instance environment must not replace %s", k)
		}
		env = append(env, k+"="+v)
	}
	env = append(env, "CONCORDE3_INSTANCE="+s.Dir)
	return env, nil
}
func processStart(pid int) string {
	b, e := os.ReadFile(fmt.Sprintf("/proc/%d/stat", pid))
	if e != nil {
		return ""
	}
	i := strings.LastIndex(string(b), ")")
	if i < 0 {
		return ""
	}
	f := strings.Fields(string(b)[i+1:])
	if len(f) < 20 {
		return ""
	}
	return f[19]
}
func Alive(p Process) bool { return p.PID > 1 && p.Start != "" && processStart(p.PID) == p.Start }
func StopProcess(p Process) error {
	if !Alive(p) {
		return nil
	}
	return syscall.Kill(-p.PID, syscall.SIGKILL)
}
func RunCommand(ctx context.Context, args []string, dir string, env []string, input []byte, cap int, started func(Process) error) ([]byte, []byte, error) {
	if len(args) == 0 {
		return nil, nil, fmt.Errorf("empty command")
	}
	cmd := exec.CommandContext(ctx, args[0], args[1:]...)
	cmd.Dir = dir
	cmd.Env = env
	cmd.Stdin = bytes.NewReader(input)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true, Pdeathsig: syscall.SIGKILL}
	stdout := &boundedBuffer{max: cap}
	stderr := &boundedBuffer{max: 4000}
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	cmd.WaitDelay = 2 * time.Second
	if e := cmd.Start(); e != nil {
		return nil, nil, e
	}
	ref := Process{PID: cmd.Process.Pid, Start: processStart(cmd.Process.Pid)}
	if started != nil {
		if e := started(ref); e != nil {
			_ = cmd.Cancel()
			_ = cmd.Wait()
			return nil, nil, e
		}
	}
	e := cmd.Wait()
	if stdout.truncated && e == nil {
		e = fmt.Errorf("command output exceeded %d bytes", cap)
	}
	return stdout.b.Bytes(), stderr.b.Bytes(), e
}
func (s *Store) AttachProcess(id string, p Process) error {
	return s.Update("operator", "process.attached", "managed activation process", func(st *State) error {
		a := st.Activations[id]
		if a.Status != "running" || st.Mode == "frozen" {
			_ = StopProcess(p)
			return fmt.Errorf("activation no longer admitted")
		}
		a.Process = p
		st.Activations[id] = a
		return nil
	})
}
func (s *Store) SetMode(mode string) error {
	return s.Update("operator", "mode.changed", "operator "+mode, func(st *State) error {
		if st.Mode == "frozen" && mode != "frozen" {
			return fmt.Errorf("frozen experiment is terminal; seed a new instance")
		}
		st.Mode = mode
		return nil
	})
}
func (s *Store) Freeze(reason string) error {
	if e := s.Update("operator", "freeze.requested", reason, func(st *State) error { st.Mode = "frozen"; return nil }); e != nil {
		return e
	}
	st, e := s.Read()
	if e != nil {
		return e
	}
	for _, a := range st.Activations {
		if e = StopProcess(a.Process); e != nil {
			return e
		}
	}
	for _, p := range st.Programs {
		if e = StopProcess(p.Process); e != nil {
			return e
		}
	}
	return s.Update("operator", "freeze.completed", reason, func(st *State) error {
		for id, a := range st.Activations {
			if a.Status == "running" {
				a.Status = "canceled"
				a.Summary = "experiment frozen"
				a.Finished = s.Now()
				st.Activations[id] = a
			}
		}
		for id, p := range st.Programs {
			p.Enabled = false
			p.Status = "stopped"
			st.Programs[id] = p
		}
		return nil
	})
}
func (s *Store) Recover() error {
	return s.Update("operator", "runtime.recovered", "reconcile leases with process reality", func(st *State) error {
		for id, a := range st.Activations {
			if a.Status != "running" {
				continue
			}
			if !s.Now().Before(a.Deadline) || !Alive(a.Process) {
				if Alive(a.Process) {
					if e := StopProcess(a.Process); e != nil {
						return e
					}
				}
				if a.Completion != nil {
					a.Status = "completed"
					a.Phase = "completed"
				} else {
					pending(&a, s.Now())
				}
				a.Finished = s.Now()
				a.Summary = "interrupted process or expired lease; useful committed state retained"
				st.Activations[id] = a
			}
		}
		for id, p := range st.Programs {
			if p.Status == "running" && !Alive(p.Process) {
				p.Process = Process{}
				p.Status = "interrupted"
				p.NextAt = s.Now().Add(30 * time.Second)
				st.Programs[id] = p
			}
		}
		return nil
	})
}
func (s *Store) RunProgram(ctx context.Context, id string) error {
	var program Program
	var cutoff time.Time
	e := s.Update("operator", "program.started", "supervise unattended operation", func(st *State) error {
		p, ok := st.Programs[id]
		if !ok || !p.Enabled || p.Status == "running" || p.NextAt.After(s.Now()) || st.Mode == "frozen" {
			return fmt.Errorf("program not ready")
		}
		if !st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt) {
			return fmt.Errorf("freeze deadline reached")
		}
		p.Status = "running"
		cutoff = st.Config.FreezeAt
		st.Programs[id] = p
		program = p
		return nil
	})
	if e != nil {
		return e
	}
	if !cutoff.IsZero() {
		var cancel context.CancelFunc
		ctx, cancel = context.WithDeadline(ctx, cutoff)
		defer cancel()
	}
	env, e := s.Environment()
	if e != nil {
		_ = s.Update("operator", "program.failed", "invalid execution environment", func(st *State) error {
			p := st.Programs[id]
			p.Status = "failed"
			p.LastError = e.Error()
			p.NextAt = s.Now().Add(30 * time.Second)
			st.Programs[id] = p
			return nil
		})
		return e
	}
	env = append(env, "CONCORDE3_PURSUIT="+program.Pursuit)
	out, stderr, runErr := RunCommand(ctx, program.Command, s.Dir, env, nil, 1<<20, func(p Process) error {
		return s.Update("operator", "program.process", "managed program process", func(st *State) error {
			x := st.Programs[id]
			if st.Mode == "frozen" || !x.Enabled {
				_ = StopProcess(p)
				return fmt.Errorf("program canceled")
			}
			x.Process = p
			st.Programs[id] = x
			return nil
		})
	})
	_ = os.MkdirAll(s.path("program-logs"), 0700)
	_ = Atomic(s.path("program-logs/"+id+".log"), append(out, stderr...))
	notifyFailure := false
	e = s.Update("operator", "program.finished", "observed process outcome, not business quality", func(st *State) error {
		x := st.Programs[id]
		x.Process = Process{}
		x.LastAt = s.Now()
		x.Status = "succeeded"
		x.LastError = ""
		truncated := false
		x.LastStderr = clipContext(strings.ToValidUTF8(string(stderr), "�"), 700, &truncated)
		if st.Mode == "frozen" || !x.Enabled {
			// An intentional stop is not an observed business/program failure.
			x.Status = "stopped"
		} else if ctx.Err() != nil {
			// Supervisor shutdown preserves registration for restart, without
			// manufacturing urgent work from an operator-controlled interruption.
			x.Status = "interrupted"
		} else if runErr != nil {
			x.Status = "failed"
			x.LastError = runErr.Error()
			notifyFailure = true
		} else if len(stderr) > 0 && st.Portfolio()[program.Pursuit].Status != "stopped" {
			// A zero exit is not proof of useful operation. Surface the first
			// stderr observation once per command/intention, without classifying
			// benign diagnostics as failure or repeatedly waking on progress logs.
			key := "program.diagnostic." + Digest(Marshal([]any{id, program.Pursuit, program.Command}))
			if _, exists := st.Wakes[key]; !exists {
				st.Wakes[key] = Wake{ID: key, Pursuit: program.Pursuit, At: s.Now(), Evidence: "Program " + id + " exited zero but emitted stderr: " + x.LastStderr + ". This may be benign; inspect the actual operation before relying on it. Full latest output: .concorde2/program-logs/" + id + ".log"}
			}
		}
		x.NextAt = s.Now().Add(time.Duration(max(30, x.IntervalSeconds)) * time.Second)
		st.Programs[id] = x
		return nil
	})
	if e == nil && notifyFailure {
		_ = s.Notify(program.Pursuit, "program."+id+"."+strconv.FormatInt(s.Now().UnixNano(), 10), "Unattended program "+id+" failed; inspect its logs and customer consequences")
	}
	return e
}
func (s *Store) StopProgram(id string) error {
	return s.StopProgramAs("operator", id)
}
func (s *Store) StopProgramAs(actor, id string) error {
	var ref Process
	e := s.Update(actor, "program.disabled", "stop managed program", func(st *State) error {
		p, ok := st.Programs[id]
		if !ok {
			return fmt.Errorf("unknown program")
		}
		p.Enabled = false
		ref = p.Process
		st.Programs[id] = p
		return nil
	})
	if e != nil {
		return e
	}
	return StopProcess(ref)
}
func Marshal(v any) []byte { b, _ := json.Marshal(v); return b }
