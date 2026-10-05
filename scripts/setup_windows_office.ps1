#requires -Version 5.1
<#
Create a dedicated WSL 2 runtime for legacy Office conversion.
Run explicitly from PowerShell; existing distributions are never modified.
Ubuntu Base is verified against both a pinned digest and Canonical's HTTPS checksum list.
Sources:
  https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/
  https://learn.microsoft.com/windows/wsl/wsl-config
#>
[CmdletBinding()]
param(
    [ValidatePattern('^LearnMargin-Office(?:-Audit-[a-f0-9]{8,32})?$')]
    [string] $Distribution = 'LearnMargin-Office',
    [string] $StorageRoot = (Join-Path $env:LOCALAPPDATA 'LearnMargin\office')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$wslCommand = Join-Path $env:SystemRoot 'System32\wsl.exe'
if (-not (Test-Path -LiteralPath $wslCommand -PathType Leaf)) {
    throw 'WSL is unavailable. Install WSL 2 before running this script.'
}

function Assert-PlainPath([string] $Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -Force -LiteralPath $current
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a redirected installation path: $current"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Get-DistributionNames {
    $listing = & $wslCommand --list --quiet
    if ($LASTEXITCODE -ne 0) { throw 'WSL is unavailable. Install WSL 2 before running this script.' }
    @($listing | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ })
}

function Invoke-DistributionScript([string] $Body, [string] $User = 'root') {
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $wslCommand
    # Distribution is validated above; User is a fixed, internal value.
    $start.Arguments = "--distribution $Distribution --user $User --cd / --exec /bin/sh -s"
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardInput = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'Could not start the dedicated WSL distribution.' }
        $process.StandardInput.Write($Body.Replace("`r", ''))
        $process.StandardInput.Close()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw "Dedicated WSL setup failed (exit $($process.ExitCode))." }
    }
    finally { $process.Dispose() }
}

if ((Get-DistributionNames) -contains $Distribution) {
    throw "Distribution '$Distribution' already exists; it will not be overwritten."
}
$StorageRoot = [IO.Path]::GetFullPath($StorageRoot)
Assert-PlainPath $StorageRoot
$installation = Join-Path $StorageRoot $Distribution
if (Test-Path -LiteralPath $installation) {
    throw "Installation path already exists; it will not be overwritten: $installation"
}
$cache = Join-Path $StorageRoot 'downloads'
Assert-PlainPath $cache
New-Item -ItemType Directory -Force -Path $cache | Out-Null

$architecture = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') {
    'arm64'
} else { 'amd64' }
$digests = @{
    amd64 = 'e77b6f10c2590cef872b33ee9f635a0e3fd1f57fb074c0e52b5c7f56147a0c86'
    arm64 = 'a91d5a93010193712d346d761372b7c9db6dfcf093893161c64ca107f05914f2'
}
$imageName = "ubuntu-base-24.04.5-base-$architecture.tar.gz"
$baseUrl = 'https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release'
$image = Join-Path $cache $imageName
Assert-PlainPath $image
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$checksums = (Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/SHA256SUMS").Content
if ($checksums -is [byte[]]) { $checksums = [Text.Encoding]::UTF8.GetString($checksums) }
$expectedLine = $digests[$architecture] + ' *' + $imageName
if (@($checksums -split "`n" | ForEach-Object { $_.Trim() }) -notcontains $expectedLine) {
    throw 'The pinned Ubuntu image is not present in the official checksum list.'
}
if (-not (Test-Path -LiteralPath $image)) {
    Write-Host "Downloading $imageName"
    Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/$imageName" -OutFile $image
}
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $image).Hash.ToLowerInvariant() -ne $digests[$architecture]) {
    throw "Ubuntu image checksum mismatch. No distribution was imported: $image"
}
Set-Content -LiteralPath (Join-Path $cache 'SHA256SUMS.verified') -Value $expectedLine -Encoding ASCII

# Check again after the download. Import never replaces an existing registration.
if ((Get-DistributionNames) -contains $Distribution) { throw 'The distribution was created by another process.' }
New-Item -ItemType Directory -Path $installation | Out-Null
Write-Host "Creating $Distribution"
& $wslCommand --import $Distribution $installation $image --version 2
if ($LASTEXITCODE -ne 0) { throw 'WSL import failed; no existing distribution was modified.' }

try {
    # The first boot only writes configuration in this new, empty runtime. There
    # are no user documents here. Terminate only this distribution before parsing
    # any materials so drive/interop settings take effect.
    Invoke-DistributionScript @'
set -eu
umask 022
cat > /etc/wsl.conf <<'CONF'
[automount]
enabled=false
mountFsTab=false
ldconfig=false
[interop]
enabled=false
appendWindowsPath=false
[boot]
systemd=false
[gpu]
enabled=false
[user]
default=learnmargin
CONF
if ! id learnmargin >/dev/null 2>&1; then
    useradd --create-home --shell /bin/sh learnmargin
fi
'@
    & $wslCommand --terminate $Distribution
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the new distribution to apply its configuration.' }
    Write-Host 'Installing the document converter and fonts'
    Invoke-DistributionScript @'
set -eu
export DEBIAN_FRONTEND=noninteractive
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
# Package maintainer scripts must not start background services in this runtime.
printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
chmod 0755 /usr/sbin/policy-rc.d
apt-get update
apt-get install -y --no-install-recommends python3 bubblewrap libreoffice-writer libreoffice-impress fonts-dejavu-core fonts-liberation fonts-noto-cjk ca-certificates
apt-get clean
install -d -m 0755 /opt/learnmargin-office
'@
    Invoke-DistributionScript -User 'learnmargin' -Body @'
set -eu
test "$(id -u)" -ne 0
test ! -e /mnt/c/Windows
# binfmt_misc registrations can be shared by concurrently running WSL distros.
# Do not disable a shared registration; this distro must expose no interop socket.
test -z "${WSL_INTEROP-}"
command -v python3
command -v bwrap
command -v libreoffice
# Prove unprivileged user namespaces work without changing global AppArmor or
# sysctl policy. An unsupported host must fail setup, not fall back unsandboxed.
bwrap --unshare-user --unshare-pid --unshare-net --unshare-ipc --unshare-uts --die-with-parent --new-session --ro-bind /usr /usr --symlink usr/bin /bin --symlink usr/lib /lib --symlink usr/lib64 /lib64 --proc /proc --dev /dev --tmpfs /tmp --cap-drop ALL --disable-userns --assert-userns-disabled /usr/bin/true
python3 --version
bwrap --version
libreoffice --headless --version
'@
    # Publish readiness only after the non-root sandbox probe succeeds.
    Invoke-DistributionScript @'
set -eu
printf 'LearnMargin-Office-v1\n' > /etc/learnmargin-office-release
chown root:root /etc/learnmargin-office-release
chmod 0644 /etc/learnmargin-office-release
'@
    Write-Host "Office runtime ready: $Distribution"
    if ($Distribution -ne 'LearnMargin-Office') {
        Write-Host "For this audit runtime, set LEARNMARGIN_OFFICE_WSL_DISTRIBUTION=$Distribution"
    }
}
catch {
    # Keep partial installation for inspection; never unregister or delete data
    # automatically. The existing user's other distributions are untouched.
    Write-Warning "Setup did not finish. Only '$Distribution' was created or modified."
    throw
}
