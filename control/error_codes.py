# Centralized Error Codes for Operational and Infrastructure Failures

CONFIG_001 = "CONFIG_001"  # Python version unsupported
CONFIG_002 = "CONFIG_002"  # Git executable missing or invalid
CONFIG_003 = "CONFIG_003"  # Missing projects configuration
CONFIG_004 = "CONFIG_004"  # Invalid projects schema
CONFIG_005 = "CONFIG_005"  # Missing Supabase configuration
CONFIG_006 = "CONFIG_006"  # Missing Supabase URL
CONFIG_007 = "CONFIG_007"  # Missing Supabase Service Key
CONFIG_008 = "CONFIG_008"  # Missing Bridge Token
CONFIG_009 = "CONFIG_009"  # Workspace folder creation failed

WORKER_001 = "WORKER_001"  # Worker startup failure
WORKER_002 = "WORKER_002"  # Task processing failed

SESSION_001 = "SESSION_001"  # Session database operation failed
SESSION_002 = "SESSION_002"  # Workspace lock acquisition failed
SESSION_003 = "SESSION_003"  # Session validation metadata retrieval failed
SESSION_004 = "SESSION_004"  # Persistent conversation expired

SUPABASE_001 = "SUPABASE_001"  # Supabase client initialization failure
SUPABASE_002 = "SUPABASE_002"  # Supabase query or mutate failure
SUPABASE_003 = "SUPABASE_003"  # Supabase heartbeat update failed

GIT_001 = "GIT_001"  # Workspace dirty check failed (dirty working directory)
GIT_002 = "GIT_002"  # Git branch creation or checkout failed
GIT_003 = "GIT_003"  # Git commit execution failed
GIT_004 = "GIT_004"  # Git push to remote repository rejected or failed

VERIFIER_001 = "VERIFIER_001"  # Completion receipt file missing or corrupted
VERIFIER_002 = "VERIFIER_002"  # Verification target files unchanged in Git status
VERIFIER_003 = "VERIFIER_003"  # Verification validation command execution failed

NETWORK_001 = "NETWORK_001"  # Network socket connection timeout or DNS resolution failure
UNKNOWN_001 = "UNKNOWN_001"  # Uncaught exception
METRICS_001 = "METRICS_001"  # Metrics database persistence failed
HEALTH_001 = "HEALTH_001"    # Health check execution failed
