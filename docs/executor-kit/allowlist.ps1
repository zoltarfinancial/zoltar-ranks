<#
    allowlist.ps1 — the refusal rules, as CODE.
    -------------------------------------------
    Dot-sourced by run-order.ps1. Kept in its own file so the rules can be read
    and reviewed without reading the loop, and so a change to the rules is a
    visible diff on its own.

    Written by zoltargenesis/claude-code, 2026-09-07, for away-mode.

    WHY THIS IS NOT A PROMPT
    ------------------------
    An instruction in a system prompt is a request to a model. These are
    conditionals in a script that runs before the model is invoked and again
    after it exits. A model that is confused, jailbroken by the text of an
    order, or simply wrong cannot talk its way past an `if`.

    THREE LAYERS, because no single one is sufficient:

      L1  STATIC SCREEN (here, pre-flight) — the order text is matched against
          the rules below BEFORE claude is launched. A hit means claude is never
          started at all.

      L2  RUNTIME CONFINEMENT (run-order.ps1) — claude runs with
          --permission-prompts none, so anything that would raise a prompt is
          denied automatically rather than waiting for a human who is not there;
          a restricted tool set; --add-dir confinement; and a dollar cap.

      L3  POST-RUN GATE (run-order.ps1) — after claude exits and BEFORE anything
          is pushed, the working tree is re-inspected: branch, what paths
          changed, and whether any protected file moved. The executor does the
          pushing, never the agent. An agent that ignored every instruction
          still cannot get a byte to the remote without passing this.

    L1 is a filter on INTENT and will produce false positives on innocent
    wording. That is the correct trade for an unattended machine: a refused
    order costs one cycle and produces a receipt saying exactly which rule fired
    so a human can rephrase. A wrongly-executed order costs whatever it touched,
    with nobody watching.
#>

# Each rule: id, human-readable what, and a regex over the order's full text.
$script:ZG_DENY_RULES = @(
    @{ id = 'DENY-MONEY'
       what = 'money, brokerage APIs, or trading'
       rx = '(?i)\b(robinhood|webull|ameritrade|td\s*ameritrade|etrade|e-trade|alpaca|coinbase|binance|kraken|interactive\s*brokers|schwab|fidelity)\b|\b(place|submit|execute|cancel)\s+(a\s+)?(trade|order|buy|sell)\b|\b(buy|sell)\s+(order|shares|stock|crypto|position)\b|\bbrokerage\b|\bwithdraw\b|\btransfer\s+funds\b|\bwire\s+transfer\b|\bpayment\b|\bcredit\s*card\b|\breal\s+money\b|\blive\s+trading\b' }

    @{ id = 'DENY-CREDENTIALS'
       what = 'reading or writing credentials or secrets'
       rx = '(?i)\.env\b|\bcredentials?\.(json|py|toml|yaml|yml)\b|\bStockPicker\b|\b(api[_\s-]?key|secret[_\s-]?key|access[_\s-]?token|refresh[_\s-]?token|private[_\s-]?key|password|passwd|passphrase)\b|\.credentials\.json|\bcmdkey\b|\bcredential\s*manager\b|\bDPAPI\b|\bkeychain\b|\bid_rsa\b|\.pem\b|\.pfx\b' }

    @{ id = 'DENY-MAIN'
       what = 'any write to main / trunk'
       rx = '(?i)\bpush\b[^.\n]{0,40}\bmain\b|\bmain\b[^.\n]{0,20}\bbranch\b[^.\n]{0,20}\b(push|commit|merge)\b|\bcommit\b[^.\n]{0,30}\bto\s+main\b|\bmerge\b[^.\n]{0,30}\b(into|to)\s+(main|trunk|master)\b|\bgit\s+push\s+\S+\s+main\b|\bforce[- ]push\b|\bpush\s+--force\b|--force-with-lease|\bcheckout\s+main\b[^.\n]{0,40}\b(commit|push)\b' }

    @{ id = 'DENY-INSTALL'
       what = 'installing or updating software'
       rx = '(?i)\b(pip|pip3)\s+install\b|\bnpm\s+(i|install|add)\b|\byarn\s+add\b|\bconda\s+install\b|\bchoco\s+install\b|\bwinget\s+install\b|\bapt(-get)?\s+install\b|\bInstall-Module\b|\bInstall-Package\b|\bdotnet\s+add\s+package\b|\bcargo\s+install\b|\bgo\s+install\b|\binstall\s+(the\s+)?(software|package|dependency|dependencies)\b|\bclaude\s+(install|update|upgrade)\b' }

    @{ id = 'DENY-SYSTEM-CONFIG'
       what = 'changing power settings, scheduled tasks, services, or the identity card'
       rx = '(?i)\bpowercfg\b|\bschtasks\b|\b(Register|Unregister|Set|Disable|Enable|Start|Stop)-ScheduledTask\b|\bscheduled\s+task\b|\bSet-Service\b|\bsc\.exe\b|\bNew-Service\b|\bidentity\.json\b|\battested_hostnames?\b|\bregistry\b|\breg\s+(add|delete)\b|\bSet-ItemProperty\b[^.\n]{0,30}HKLM|\bbcdedit\b|\bgpedit\b|\bnetsh\b|\bfirewall\b|\bDefender\b|\brename[- ]?computer\b|\bRename-Computer\b' }

    @{ id = 'DENY-DESTRUCTIVE'
       what = 'deleting outside the scratch directory'
       rx = '(?i)\brm\s+-rf?\b|\bRemove-Item\b[^.\n]{0,60}-Recurse|\bdel\s+/[sq]\b|\brmdir\s+/s\b|\bformat\s+[a-z]:|\bgit\s+clean\s+-[a-z]*d|\bgit\s+reset\s+--hard\b|\bdrop\s+(table|database)\b|\btruncate\s+table\b|\bdelete\s+(the\s+)?(repo|repository|clone|workspace|everything|all\s+files)\b|\btrash_file\b|\bwipe\b' }

    @{ id = 'DENY-SELF-EXCEPTION'
       what = 'an order arguing for its own exception to these rules'
       rx = '(?i)\b(ignore|bypass|skip|disable|override|relax|suspend|turn\s+off|work\s+around|circumvent)\b[^.\n]{0,60}\b(allowlist|allow[- ]list|deny[- ]?list|rule|restriction|guard|safety|guardrail|policy|check|gate|refus\w+|permission)\b|\b(this|it)\s+is\s+(an\s+)?(exception|authorised|authorized|pre-?approved|fine|safe|ok)\b|\byou\s+(may|can|should)\s+ignore\b|\bdespite\s+the\s+(rules?|allowlist|policy)\b|\bdangerously-skip-permissions\b|\bbypassPermissions\b|\bAndrew\s+(said|says|approved|authorised|authorized)\b[^.\n]{0,60}\b(exception|ignore|bypass|skip)\b|\bemergency\s+override\b|\bdo\s+not\s+write\s+a\s+receipt\b|\bdo\s+not\s+log\b|\bdelete\s+(the\s+)?(receipt|log|evidence)\b' }

    @{ id = 'DENY-EXFIL'
       what = 'sending data off this machine outside git'
       rx = '(?i)\b(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b[^.\n]{0,80}\b(post|put|-Method|--data|-d\s)|\bsend\s+(an?\s+)?(email|mail)\b|\bsmtp\b|\bpastebin\b|\bngrok\b|\bupload\s+.{0,30}\bto\s+(http|ftp|s3|bucket|drive)\b|\bexfiltrat\w+\b|\bbase64\b[^.\n]{0,40}\b(post|send|upload)\b' }
)

function Test-OrderAllowed {
    <#
      Returns a PSCustomObject: allowed(bool), rule_id, rule_what, evidence.
      `evidence` is the literal matched substring, so a receipt can quote the
      exact words that triggered the refusal rather than just naming a rule.
      That is what makes a false positive cheap to fix: the human sees the
      phrase to rewrite.
    #>
    param([Parameter(Mandatory)][AllowEmptyString()][string]$Text)

    foreach ($rule in $script:ZG_DENY_RULES) {
        $m = [regex]::Match($Text, $rule.rx)
        if ($m.Success) {
            $ev = $m.Value
            if ($ev.Length -gt 160) { $ev = $ev.Substring(0,160) + '...' }
            return [pscustomobject]@{
                allowed   = $false
                rule_id   = $rule.id
                rule_what = $rule.what
                evidence  = $ev
            }
        }
    }
    return [pscustomobject]@{ allowed = $true; rule_id = $null; rule_what = $null; evidence = $null }
}

# Paths the post-run gate permits an order to have modified. Anything else in
# the diff fails the gate and nothing is pushed.
$script:ZG_WRITABLE_PREFIXES = @(
    'src/', 'scripts/', 'tests/', 'docs/', 'config/', 'data/review/', 'data/fleet/', 'orders/'
)

# Files whose size+mtime are snapshotted before the run and compared after.
# These are never read — only stat'd. A change here fails the gate loudly.
function Get-ProtectedWitness {
    $paths = @(
        'C:\Users\apod7\StockPicker\.env',
        'C:\Users\apod7\StockPicker\credentials.py',
        'C:\Shared\ZoltarUnlimited\fleet\identity.json',
        'C:\Shared\ZoltarUnlimited\fleet\heartbeat.ps1',
        'C:\Shared\ZoltarUnlimited\fleet\executor\allowlist.ps1',
        'C:\Shared\ZoltarUnlimited\fleet\executor\run-order.ps1'
    )
    $w = @{}
    foreach ($p in $paths) {
        try {
            if (Test-Path $p) {
                $i = Get-Item $p -Force
                $w[$p] = "$($i.Length):$($i.LastWriteTimeUtc.Ticks)"
            } else { $w[$p] = 'absent' }
        } catch { $w[$p] = "stat-failed" }
    }
    return $w
}

function Compare-ProtectedWitness {
    param([hashtable]$Before, [hashtable]$After)
    $changed = @()
    foreach ($k in $Before.Keys) {
        if ($Before[$k] -ne $After[$k]) { $changed += $k }
    }
    return $changed
}
