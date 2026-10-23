# test_media/

Local video files for manual runs and the opt-in smoke tests. **Nothing in
this folder is ever committed** except this README: the media is copyrighted
and far too large for git. `.gitignore` ignores everything else in here, at
any depth.

Unit tests never read this folder (they use temporary directories and mocks,
see AGENTS.md). Anything that needs real media is a smoke test and must skip
cleanly when the files are absent.
