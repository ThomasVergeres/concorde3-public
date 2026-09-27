package core

import (
	"testing"
	"time"
)

func TestRecoveryBackoffCapsAtTenMinutes(t *testing.T) {
	now := time.Now().UTC()
	for attempt, minutes := range []int{1, 2, 4, 8, 10, 10, 10, 10} {
		a := Activation{RecoveryAttempts: attempt}
		pending(&a, now)
		if a.RetryAt.Sub(now) != time.Duration(minutes)*time.Minute || a.Phase != "rectification_pending" || a.Status != "failed" {
			t.Fatalf("attempt %d: %+v", attempt, a)
		}
	}
}
