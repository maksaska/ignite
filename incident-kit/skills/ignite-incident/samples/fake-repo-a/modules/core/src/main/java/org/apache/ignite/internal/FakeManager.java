package org.apache.ignite.internal;

public class FakeManager {
    private static final String SHARED_MSG = "Local node SEGMENTED: fake";

    public void run() {
        log.warning("Fake subsystem entered degraded state");
        U.warn(log, "Fake ring message send failed [next=" + next + ']');
        if (bad)
            throw new IgniteCheckedException("Fake unrecoverable condition detected");
    }
}
