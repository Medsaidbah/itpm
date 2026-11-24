<#
Migration rehearsal on a copy of the live database (see CLAUDE.md, "Migration rehearsal").

    powershell -ExecutionPolicy Bypass -File scripts\rehearse-migrations.ps1 -DumpPath C:\projects\live-copy\backup_itpm.sql

What it does, in a Docker stack of its own (project "itpm-rehearsal": its own containers, network and volumes,
port 8001, emails only written to the log), never touching the normal stack:
  1. builds the current branch as the image itpm-rehearsal:local (your itpm:<version> image is not replaced);
  2. starts the rehearsal database, empties it (DROP SCHEMA public, then an empty public schema again) and
     restores the dump with psql;
  3. counts the rows of every table, lists the migrations still to apply, applies them, and checks:
     migrate --check, the Django system check, the same row counts afterwards;
  4. starts the app on http://localhost:8001, checks /healthz/ and the login page, and opens the main pages
     as an admin and a user of the copy: each must answer 200 (a redirect is a failure, shown with its path);
     from release 1.2, an executive made for the check opens the read pages (200), every change it tries must be
     refused (403), and nothing may change; from release 1.3, a marker budget on one project (removed afterwards)
     must be hidden from an ordinary member of its team and shown to its manager, an admin and an executive.
  Every old value is checked with a fingerprint (a hash per table) before and after migrating.
The report (counts, migration names and status codes only, never names or emails) is shown and saved in
%TEMP%\itpm-rehearsal. The dump is copied into the database container only, never into the project folder.
The stack keeps running for a look; stop it with the "down" command printed at the end (it keeps the volumes).
#>
param(
    [string]$DumpPath = "",
    [switch]$SelfTest  # only checks the error filter below, without Docker (used by the app's tests)
)

$ProjectName = "itpm-rehearsal"
$Port = 8001
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Work = Join-Path $env:TEMP "itpm-rehearsal"
$EnvFile = Join-Path $Work "rehearsal.env"
$Report = New-Object System.Collections.Generic.List[string]
$script:Failed = $false

function Say([string]$text, [string]$colour = "Gray") {
    Write-Host $text -ForegroundColor $colour
    $Report.Add($text)
}
function Check([string]$name, [bool]$pass, [string]$detail = "") {
    if ($pass) { Say ("[ OK ] " + $name + " " + $detail) "Green" }
    else { Say ("[FAIL] " + $name + " " + $detail) "Red"; $script:Failed = $true }
}
function Save-Report {
    New-Item -ItemType Directory -Force $Work | Out-Null
    $file = Join-Path $Work ("report-" + (Get-Date -Format "yyyyMMdd-HHmm") + ".txt")
    [IO.File]::WriteAllLines($file, $Report)
    Write-Host ("Report saved: " + $file) -ForegroundColor Cyan
}
function Stop-Here([string]$why) {
    Check $why $false
    Save-Report
    exit 1
}
# docker compose, always with the rehearsal project name, both compose files and the rehearsal settings
function Compose {
    & docker compose -p $ProjectName -f (Join-Path $Repo "docker-compose.yml") -f (Join-Path $Repo "docker-compose.rehearsal.yml") --env-file $EnvFile @args
}
function Read-Counts([string[]]$lines) {
    $table = @{}
    foreach ($line in $lines) {
        $parts = "$line".Trim() -split " "
        if ($parts.Count -eq 2 -and $parts[1] -match '^\d+$') { $table[$parts[0]] = [int]$parts[1] }
    }
    return $table
}


# Runs docker compose and keeps what it printed: Out (normal output), Err (error output), Ok (exit code 0)
function Invoke-Captured {
    $all = Compose @args 2>&1
    $code = $LASTEXITCODE
    $out = @($all | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    $err = @($all | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { "$_" })
    return @{ Out = $out; Err = $err; Ok = ($code -eq 0) }
}
# The last error lines of a failed step, for the report. Only the exception lines are kept: never the "DETAIL:" or
# "Key (...)=(...)" lines of a database error, which can repeat row values; emails and passwords are masked.
function Select-ErrorLines([string[]]$lines, [int]$last = 3) {
    $picked = @($lines | Where-Object {
            $_ -match '(Error|Exception|FATAL)\b' -and $_ -notmatch '^\s*(DETAIL|HINT|Key \()' -and $_ -notmatch '^\s*File "'
        } | ForEach-Object {
            $text = $_.Trim() -replace 'Key \(.*?\)=\(.*?\)', 'Key (<hidden>)'
            $text = $text -replace '[^\s"''<>()]+@[^\s"''<>()]+', '<email hidden>'
            $text = $text -replace '(?i)(password\s*[=:]\s*)\S+', '$1<hidden>'
            if ($text.Length -gt 300) { $text.Substring(0, 300) + "..." } else { $text }
        })
    return @($picked | Select-Object -Last $last)
}
function Show-Errors($result) {
    $lines = Select-ErrorLines (@($result.Err) + @($result.Out))
    if ($lines.Count -eq 0) { Say "    (no error line found in the output)" "Red" }
    foreach ($line in $lines) { Say ("    error: " + $line) "Red" }
}

if ($SelfTest) {
    $sample = @(
        'Traceback (most recent call last):',
        '  File "/app/manage.py", line 22, in <module>',
        'psycopg.OperationalError: connection failed: FATAL:  password authentication failed for user "itpm_user"',
        'django.db.utils.IntegrityError: duplicate key value violates unique constraint "accounts_user_email_key"',
        'DETAIL:  Key (email)=(sara@example.com) already exists.',
        'ValueError: bad value from sara@example.com with password=abc123 and Key (email)=(omar@example.com)',
        'Applying accounts.0008... OK')
    Select-ErrorLines $sample 10 | ForEach-Object { Write-Output $_ }
    exit 0
}

Say ("Migration rehearsal - " + (Get-Date -Format "yyyy-MM-dd HH:mm") + " - branch " + (& git -C $Repo rev-parse --abbrev-ref HEAD) + " at " + (& git -C $Repo rev-parse --short HEAD)) "Cyan"

# ---------- 0. Safety checks ----------
if (-not $DumpPath) { Stop-Here "-DumpPath is given (the path of the dump)" }
if (-not (Test-Path -LiteralPath $DumpPath -PathType Leaf)) { Stop-Here ("the dump file exists (" + $DumpPath + ")") }
$Dump = (Resolve-Path -LiteralPath $DumpPath).Path
Check "the dump is outside the project folder" (-not $Dump.StartsWith($Repo + "\", [StringComparison]::OrdinalIgnoreCase))
if ($script:Failed) { Stop-Here "never use a dump inside the project folder" }
$head = Get-Content -LiteralPath $Dump -TotalCount 5
Check "the dump is a plain SQL pg_dump" (($head -join "`n") -match "PostgreSQL database dump")
if ($script:Failed) { Stop-Here "the dump must be a plain SQL file made by pg_dump" }
Say ("Dump size: {0:N0} bytes" -f (Get-Item -LiteralPath $Dump).Length)

# The rehearsal settings, outside the project folder: random secrets, emails only in the log, port 8001
New-Item -ItemType Directory -Force $Work | Out-Null
$secret = [Convert]::ToBase64String((1..48 | ForEach-Object { [byte](Get-Random -Maximum 256) }))
$dbPassword = -join (1..32 | ForEach-Object { [char[]]"abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789" | Get-Random })
[IO.File]::WriteAllLines($EnvFile, @(
    "SECRET_KEY=$secret", "DEBUG=False", "ALLOWED_HOSTS=localhost,127.0.0.1,testserver", "HTTPS=False",
    "HSTS_SECONDS=0", "SITE_URL=http://localhost:$Port", "TRUSTED_PROXY=127.0.0.1",
    "DB_NAME=itpm", "DB_USER=itpm_user", "DB_PASSWORD=$dbPassword", "DB_HOST=db", "DB_PORT=5432",
    "EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend", "DEFAULT_FROM_EMAIL=rehearsal@localhost",
    "EMAIL_HOST=localhost", "EMAIL_HOST_USER=", "EMAIL_HOST_PASSWORD=",
    "GRAPH_TENANT_ID=", "GRAPH_CLIENT_ID=", "GRAPH_CLIENT_SECRET=", "GRAPH_SENDER=",
    "DJANGO_SUPERUSER_USERNAME=", "DJANGO_SUPERUSER_EMAIL=", "DJANGO_SUPERUSER_PASSWORD=",
    "BIND_ADDRESS=127.0.0.1", "APP_PORT=$Port"))

# A previous rehearsal still running: stop its containers (its volumes are kept; nothing else is touched)
Compose down 2>$null | Out-Null
$busy = $false
try { $probe = New-Object System.Net.Sockets.TcpClient; $probe.Connect("127.0.0.1", $Port); $busy = $true; $probe.Close() } catch { }
Check ("port " + $Port + " is free") (-not $busy)
if ($script:Failed) { Stop-Here ("something else uses port " + $Port) }

# ---------- 1. Build the branch ----------
Say "Building the image itpm-rehearsal:local from this branch..." "Cyan"
Compose build web | Out-Null
Check "image built" ($LASTEXITCODE -eq 0)
if ($script:Failed) { Stop-Here "the image could not be built" }

# ---------- 2. The rehearsal database: start, empty, restore ----------
Compose up -d db 2>$null | Out-Null
$dbId = (Compose ps -q db) | Select-Object -First 1
$dbName = if ($dbId) { (& docker inspect --format "{{.Name}}" $dbId) } else { "" }
Check "the database container belongs to the rehearsal" ($dbName -like "/$ProjectName-*") $dbName
if ($script:Failed) { Stop-Here "refusing to touch a database that isn't the rehearsal's" }
$health = ""
for ($i = 0; $i -lt 30 -and $health -ne "healthy"; $i++) {
    Start-Sleep -Seconds 2
    $health = & docker inspect --format "{{.State.Health.Status}}" $dbId
}
Check "rehearsal database ready" ($health -eq "healthy") $health
if ($script:Failed) { Stop-Here "the rehearsal database didn't start" }

# The volume keeps the password of the run that created it (PostgreSQL reads POSTGRES_PASSWORD only then), and each
# run makes a new one: give itpm_user this run's password, through the container's local socket, so the app connects
Compose exec -T db psql -U itpm_user -d itpm -v ON_ERROR_STOP=1 -q -c "ALTER USER itpm_user WITH PASSWORD '$dbPassword';" | Out-Null
Check "database password set to this run's settings" ($LASTEXITCODE -eq 0)
if ($script:Failed) { Stop-Here "the rehearsal database password could not be set" }

# The dump has \restrict lines (PostgreSQL 16.10 and newer): an older psql can't read it
$psql = "$(Compose exec -T db psql --version)"
$psqlOk = $false
if ($psql -match '(\d+)\.(\d+)') { $psqlOk = ([int]$Matches[1] -gt 16) -or (([int]$Matches[1] -eq 16) -and ([int]$Matches[2] -ge 10)) }
Check "psql can read the dump (16.10 or newer)" $psqlOk $psql
if ($script:Failed) { Stop-Here "run: docker pull postgres:16   then run this script again" }

# Empty the rehearsal database: the dump has no CREATE SCHEMA, so the empty public schema is made again
Compose exec -T db psql -U itpm_user -d itpm -v ON_ERROR_STOP=1 -q -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;" | Out-Null
Check "rehearsal database emptied" ($LASTEXITCODE -eq 0)
Compose cp $Dump db:/tmp/rehearsal.sql | Out-Null
Compose exec -T db psql -U itpm_user -d itpm -v ON_ERROR_STOP=1 -q -f /tmp/rehearsal.sql | Out-Null
$restored = ($LASTEXITCODE -eq 0)
Compose exec -T db rm -f /tmp/rehearsal.sql | Out-Null
Check "dump restored with psql" $restored
if ($script:Failed) { Stop-Here "the dump could not be restored" }

# ---------- 3. Before, migrate, after ----------
$counting = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py counts
$before = Read-Counts $counting.Out
Check "rows counted before migrating" ($counting.Ok -and $before.Count -gt 0) ("(" + $before.Count + " tables)")
if (-not $counting.Ok -or $before.Count -eq 0) { Show-Errors $counting; Stop-Here "the app can't read the restored database" }
# A fingerprint of every table over its present columns: compared after migrating, it proves no old value changed
$fingerprint = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py fingerprint
$printsBefore = @{}
foreach ($line in $fingerprint.Out) {
    $parts = @("$line".Trim() -split " ")
    if ($parts.Count -eq 3) { $printsBefore[$parts[0]] = @($parts[1], $parts[2]) }
}
Check "fingerprints taken before migrating" ($fingerprint.Ok -and $printsBefore.Count -gt 0) ("(" + $printsBefore.Count + " tables)")
if (-not $fingerprint.Ok) { Show-Errors $fingerprint }
# Every project's progress and expected progress, kept to compare after migrating (release 1.4: they must not move)
$progressBefore = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py progress
$progressLines = @($progressBefore.Out | Where-Object { "$_" -match '^\d+:\d+:(\d+|None)$' } | ForEach-Object { "$_".Trim() })
Check "progress worked out before migrating" $progressBefore.Ok ("(" + $progressLines.Count + " projects)")
if (-not $progressBefore.Ok) { Show-Errors $progressBefore }
$plan = Compose run --rm --no-deps web python manage.py showmigrations --plan 2>$null
$pending = @($plan | Where-Object { "$_" -match '^\[ \]' } | ForEach-Object { ("$_" -replace '^\[ \]\s*', '').Trim() })
Say ("Migrations to apply: " + $(if ($pending.Count) { $pending -join ", " } else { "none" }))

$migrate = Invoke-Captured run --rm --no-deps web python manage.py migrate --noinput
foreach ($line in $migrate.Out) { if ($line -match "Applying|No migrations to apply") { Say ("  " + $line.Trim()) } }
Check "migrate" $migrate.Ok
if (-not $migrate.Ok) { Show-Errors $migrate; Stop-Here "the migrations failed on the copy of the live database" }

$migrateCheck = Invoke-Captured run --rm --no-deps web python manage.py migrate --check
Check "migrate --check (nothing left to apply)" $migrateCheck.Ok
if (-not $migrateCheck.Ok) { Show-Errors $migrateCheck }
$systemCheck = Invoke-Captured run --rm --no-deps web python manage.py check
Check "Django system check" $systemCheck.Ok
if (-not $systemCheck.Ok) { Show-Errors $systemCheck }

# The same fingerprints after migrating, over the columns that existed before: every old value must be the same
$specs = @($printsBefore.Keys | Sort-Object | ForEach-Object { $_ + "=" + $printsBefore[$_][1] })
$fingerprintAfter = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py fingerprint-of @specs
$changed = 0
foreach ($line in $fingerprintAfter.Out) {
    $parts = @("$line".Trim() -split " ")
    if ($parts.Count -ne 2 -or -not $printsBefore.ContainsKey($parts[0])) { continue }
    if ($parts[1] -ne $printsBefore[$parts[0]][0]) { Check ("old data untouched in " + $parts[0]) $false $parts[1]; $changed++ }
}
Check "old data untouched (fingerprints before = after)" ($fingerprintAfter.Ok -and $changed -eq 0) ("(" + $printsBefore.Count + " tables)")
if (-not $fingerprintAfter.Ok) { Show-Errors $fingerprintAfter }

# Every project's progress and expected progress, the same as before migrating
$progressAfter = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py progress-compare @progressLines
foreach ($line in $progressAfter.Out) {
    if ("$line" -match '^progress-same (\S+)') { Check "every project keeps its progress and expected progress" ($Matches[1] -eq "ok") $Matches[1] }
}
if (-not $progressAfter.Ok) { Check "progress compared" $false; Show-Errors $progressAfter }

# Release 1.3 and later: every old project and history row got the defaults (not specified, no partner, no money)
$defaults = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py defaults
foreach ($line in $defaults.Out) {
    if ("$line" -match '^projects-defaults (\S+)') { Check "old projects got the defaults (type not specified, no money)" ($Matches[1] -eq "ok") $Matches[1] }
}
if (-not $defaults.Ok) { Check "defaults checked" $false; Show-Errors $defaults }

$counting = Invoke-Captured run --rm --no-deps web python scripts/rehearsal_checks.py counts
$after = Read-Counts $counting.Out
if (-not $counting.Ok -or $after.Count -eq 0) { Check "rows counted after migrating" $false; Show-Errors $counting }
# Django's own bookkeeping grows with new migrations and models; every other table must keep its rows
$bookkeeping = @("django_migrations", "django_content_type", "auth_permission")
Say "Rows per table (before -> after):"
foreach ($table in (@($before.Keys) + @($after.Keys) | Sort-Object -Unique)) {
    $b = if ($before.ContainsKey($table)) { "" + $before[$table] } else { "new" }
    $a = if ($after.ContainsKey($table)) { "" + $after[$table] } else { "gone" }
    $same = ($b -eq $a) -or ($b -eq "new" -and $a -eq "0") -or ($bookkeeping -contains $table)
    if ($same) { Say ("  {0,-40} {1,8} -> {2}" -f $table, $b, $a) }
    else { Check ("rows kept in " + $table) $false ("{0} -> {1}" -f $b, $a) }
}

# ---------- 4. The app ----------
Compose up -d web 2>$null | Out-Null
$healthz = ""
for ($i = 0; $i -lt 30 -and $healthz -ne "ok"; $i++) {
    Start-Sleep -Seconds 2
    try { $healthz = (Invoke-WebRequest "http://127.0.0.1:$Port/healthz/" -UseBasicParsing -TimeoutSec 5).Content.Trim() } catch { $healthz = "" }
}
Check "app answers on /healthz/" ($healthz -eq "ok") $healthz
try { $login = (Invoke-WebRequest "http://127.0.0.1:$Port/login/" -UseBasicParsing -TimeoutSec 10).StatusCode } catch { $login = "no answer" }
Check "login page" ("$login" -eq "200") "$login"

Say "Pages opened as an admin, a user and an executive (status codes; write-... must be refused):"
$pages = Compose exec -T web python scripts/rehearsal_checks.py pages 2>$null
# Every page of the list must answer 200: a redirect (like "302 /profile/password/") or an error is a failure.
# The executive's "write-..." lines must be refused (403), and "nothing-changed" must say "ok".
foreach ($line in $pages) {
    $parts = @("$line".Trim() -split " ")
    if ($parts.Count -lt 3) { continue }
    if ($parts[1] -eq "note") { Say ("  {0,-9} note: {1}" -f $parts[0], $parts[2]) "Yellow"; continue }
    $expected = "200"
    if ($parts[1] -like "write-*") { $expected = "403" }
    if ($parts[1] -like "budget-*") { $expected = "ok" }  # release 1.3: hidden from the member, shown to the others
    if ($parts[1] -eq "nothing-changed") { $expected = "ok" }
    $result = ($parts[2..($parts.Count - 1)] -join " ")
    if ($result -eq $expected) { Say ("  {0,-9} {1,-24} {2}" -f $parts[0], $parts[1], $result) }
    else { Check ($parts[0] + " " + $parts[1]) $false ($result + " (expected " + $expected + ")") }
}
Check "pages opened" (@($pages).Count -gt 0)

if ($script:Failed) { Say "REHEARSAL FAILED - do not send this version" "Red" }
else { Say "REHEARSAL PASSED" "Green" }
Save-Report
Write-Host ""
Write-Host ("The rehearsal app keeps running on http://localhost:" + $Port + " (accounts and passwords of the copy; attachments are missing).") -ForegroundColor Cyan
Write-Host ("Stop it (keeps its volumes):  docker compose -p " + $ProjectName + " -f docker-compose.yml -f docker-compose.rehearsal.yml --env-file `"" + $EnvFile + "`" down") -ForegroundColor Cyan
if ($script:Failed) { exit 1 }
