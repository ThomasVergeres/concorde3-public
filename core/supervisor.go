package core

import (
	"context"
	"fmt"
	"os"
	"sync"
	"syscall"
	"time"
)

// Run is the single managed-process owner. State transactions remain process-safe.
func (s *Store) Run(ctx context.Context, once bool, report func(string)) error {
	f, e := os.OpenFile(s.path("supervisor.lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	if e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); e != nil {
		return fmt.Errorf("an instance supervisor already owns execution")
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	if e = s.Recover(); e != nil {
		return e
	}
	if e = s.SetMode("running"); e != nil {
		return e
	}
	if once {
		defer s.SetMode("paused")
	}
	c, cancel := context.WithCancel(ctx)
	defer cancel()
	var wg sync.WaitGroup
	defer func() { cancel(); wg.Wait() }()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	// A pulse admits only one activation, but remains its program supervisor
	// while work and rectification execute. Otherwise late registrations and
	// requested restarts cannot run until the activation has already ended.
	var onceDone chan error
	for {
		st, e := s.Read()
		if e != nil {
			return e
		}
		if st.Mode == "frozen" || (!st.Config.FreezeAt.IsZero() && !s.Now().Before(st.Config.FreezeAt)) {
			return s.Freeze("finite experiment deadline or explicit freeze")
		}
		for id, p := range st.Programs {
			if p.Enabled && p.Status != "running" && !p.NextAt.After(s.Now()) {
				wg.Add(1)
				go func(id string) {
					defer wg.Done()
					if e := s.RunProgram(c, id); e != nil && report != nil {
						report(e.Error())
					}
				}(id)
			}
		}
		if onceDone == nil {
			a, p, e := s.Admit()
			if e == nil {
				if once {
					onceDone = make(chan error, 1)
				}
				wg.Add(1)
				go func() {
					defer wg.Done()
					err := s.Execute(c, a, p)
					if once {
						onceDone <- err
					} else if err != nil && report != nil {
						report(err.Error())
					}
				}()
			} else if once {
				return e
			}
		}
		select {
		case err := <-onceDone:
			return err
		case <-ctx.Done():
			_ = s.SetMode("paused")
			return nil
		case <-ticker.C:
		}
	}
}
