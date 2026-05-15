// Search runtime facade.
//
// The actual implementation is now split across:
// - search/profiles.js
// - search/perception.js
// - search/planner.js
// - search/progress.js
// - search/executor.js
//
// This file intentionally stays light so loader-compatible consumers can keep
// referring to the primitive name `search` while the runtime loads the split
// implementation pieces recursively.
