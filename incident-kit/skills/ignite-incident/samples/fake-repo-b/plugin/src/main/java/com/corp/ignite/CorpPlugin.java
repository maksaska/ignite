package com.corp.ignite;

public class CorpPlugin {
    private static final String SHARED_MSG = "Local node SEGMENTED: fake";

    public void start() {
        log.error("Corp plugin failed to initialise security context");
        LT.warn(log, "Corp plugin degraded, falling back to defaults");
    }
}
