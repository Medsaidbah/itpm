# Checks the running Docker app, makes a fresh database dump and builds the two zip files for the manager.
# Run from C:\projects\itpm with the Docker stack running:   powershell -ExecutionPolicy Bypass -File delivery\make-manager-pack.ps1
Set-Location C:\projects\itpm
$stamp = Get-Date -Format "yyyy-MM-dd"
$bk = "C:\projects\backup"; New-Item -ItemType Directory -Force $bk | Out-Null
$script:ok = $true
function Check($name, [bool]$pass, $detail = "") {
    if ($pass) { Write-Host ("[ OK ] " + $name + " " + $detail) -ForegroundColor Green }
    else { Write-Host ("[FAIL] " + $name + " " + $detail) -ForegroundColor Red; $script:ok = $false }
}
$envText = Get-Content docker.env -Raw
$dbUser = [regex]::Match($envText, '(?m)^DB_USER=(.+?)\s*$').Groups[1].Value
$dbName = [regex]::Match($envText, '(?m)^DB_NAME=(.+?)\s*$').Groups[1].Value

$web = (docker compose --env-file docker.env ps --format "{{.Name}} {{.Image}} {{.State}}" web 2>&1) -join " "
Check "web container runs image itpm:1.0.1" (($web -match "itpm:1.0.1") -and ($web -match "running")) $web
try { $h = (Invoke-WebRequest http://localhost:8000/healthz/ -UseBasicParsing -TimeoutSec 15).Content.Trim() } catch { $h = "no answer" }
Check "health check answers ok" ($h -eq "ok") $h
$ep = (docker run --rm --entrypoint cat itpm:1.0.1 /app/docker/entrypoint.sh) -join "`n"
Check "image contains the first-admin step (version 1.0.1)" ($ep -match "createsuperuser")
docker compose --env-file docker.env exec -T web python manage.py migrate --check | Out-Null
Check "database schema is up to date (no pending migrations)" ($LASTEXITCODE -eq 0)
docker compose --env-file docker.env exec -T web python manage.py check | Out-Null
Check "Django system check" ($LASTEXITCODE -eq 0)
docker compose --env-file docker.env --env-file docker.env.example config -q 2>$null
Check "docker-compose.yml is valid" ($LASTEXITCODE -eq 0)
$mailBackend = [regex]::Match($envText, '(?m)^EMAIL_BACKEND=(.+?)\s*$').Groups[1].Value
Check "email backend is SMTP (not console)" ($mailBackend -match "smtp") $mailBackend

$cnt = docker compose --env-file docker.env exec -T db psql -U $dbUser -d $dbName -At -F " " -c "select (select count(*) from accounts_user), (select count(*) from projects_project), (select count(*) from projects_task)"
Write-Host ("Rows now: users / projects / tasks = " + $cnt) -ForegroundColor Cyan

docker compose --env-file docker.env exec -T db pg_dump -U $dbUser -d $dbName --no-owner --no-privileges -f /tmp/final.sql
$dump = "$bk\itpm-final-$stamp.sql"
docker compose --env-file docker.env cp db:/tmp/final.sql $dump
$size = if (Test-Path $dump) { (Get-Item $dump).Length } else { 0 }
Check "database dump made" (($size -gt 50000) -and (Select-String -Path $dump -Pattern "COPY public.accounts_user" -Quiet)) ("{0} bytes" -f $size)

docker save itpm:1.0.1 -o C:\projects\mchariee-1.0.1.tar
Check "image saved to tar" ((Test-Path C:\projects\mchariee-1.0.1.tar) -and ((Get-Item C:\projects\mchariee-1.0.1.tar).Length -gt 50000000))

Copy-Item $dump "$bk\mchariee-manager-data.sql" -Force
Compress-Archive -Force -DestinationPath C:\projects\mchariee-manager-pack.zip -Path C:\projects\mchariee-1.0.1.tar, docker-compose.yml, docker.env.example, DEPLOY-DOCKER.md, EMAIL-M365-SETUP.md, delivery\MANAGER-README.txt
Compress-Archive -Force -DestinationPath C:\projects\mchariee-manager-data.zip -Path "$bk\mchariee-manager-data.sql"

Add-Type -AssemblyName System.IO.Compression.FileSystem
$z = [IO.Compression.ZipFile]::OpenRead("C:\projects\mchariee-manager-pack.zip")
$names = $z.Entries | ForEach-Object { $_.Name }
$z.Dispose()
Write-Host ("Pack contains: " + ($names -join ", "))
Check "pack contains no secret file" (-not ($names -contains "docker.env" -or $names -contains ".env"))
Check "pack has all 6 files" ($names.Count -eq 6)
Get-FileHash C:\projects\mchariee-manager-pack.zip, C:\projects\mchariee-manager-data.zip -Algorithm SHA256 | Select-Object Hash, @{n="File";e={Split-Path $_.Path -Leaf}} | Format-Table -AutoSize
if ($script:ok) { Write-Host "ALL CHECKS PASSED" -ForegroundColor Green } else { Write-Host "SOME CHECKS FAILED - do not send yet" -ForegroundColor Red }
