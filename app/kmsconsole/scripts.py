"""Generates client activation scripts that point to this KMS container."""
import re

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_KEY_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")

TYPES = ["windows-cmd", "windows-ps1", "eval-cmd", "eval-ps1", "office-cmd", "office-ps1", "reg", "gpo", "dns", "status"]

FILENAMES = {
    "windows-cmd": "activate-windows.cmd",
    "windows-ps1": "activate-windows.ps1",
    "eval-cmd": "convert-eval-and-activate.cmd",
    "eval-ps1": "convert-eval-and-activate.ps1",
    "office-cmd": "activate-office.cmd",
    "office-ps1": "activate-office.ps1",
    "reg": "kms-server.reg",
    "gpo": "kms-gpo.ps1",
    "dns": "kms-dns-srv.txt",
    "status": "kms-status.cmd",
}


# Public volume license keys (GVLK) per Windows Server build, from Microsoft's KMS client key list.
SERVER_GVLK = {
    14393: ("Windows Server 2016", {"ServerStandard": "WC2BQ-8NRM3-FDDYY-2BFGV-KHKQY", "ServerDatacenter": "CB7KF-BWN84-R7R2Y-793K2-8XDDG"}),
    17763: ("Windows Server 2019", {"ServerStandard": "N69G4-B89J2-4G8F4-WWYCC-J464C", "ServerDatacenter": "WMDGN-G9PQG-XVVXX-R3X43-63DFG"}),
    20348: ("Windows Server 2022", {"ServerStandard": "VDYBN-27WPP-V4HQT-9VMD4-VMK7H", "ServerDatacenter": "WX4NM-KYWYW-QJJR4-XV3QB-6VM33"}),
    26100: ("Windows Server 2025", {"ServerStandard": "TVRH6-WHNXV-R9WG3-9XRFY-MY832", "ServerDatacenter": "D764K-2NDRG-47T6Q-P8T8W-YP6DF"}),
}
EVAL_TARGETS = ("auto", "ServerStandard", "ServerDatacenter")
EVAL_TASK = "KMS Console - finish activation"

_EVAL_PS1 = r"""#Requires -RunAsAdministrator
# KMS Console - convert Windows Server Evaluation to the full edition and activate
# Server: __HP__
# Run as administrator. Use -NoRestart to restart later yourself.
param([switch]$NoRestart, [switch]$Finish)
$ErrorActionPreference = "Stop"

$KmsHost  = "__HOST__"
$KmsPort  = __PORT__
$Kms      = "__HP__"
$Target   = "__TARGET__"   # auto, ServerStandard or ServerDatacenter
$TaskName = "__TASK__"
$Dir      = "$env:ProgramData\KMSConsole"
$Log      = "$Dir\activation.log"
$slmgr    = "$env:windir\System32\slmgr.vbs"
$WinApp   = "55c92734-d682-4d71-983e-d6ec3f16059f"

$keys = @{
__KEYS__
}

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
function Log($msg) { $line = "[{0}] {1}" -f (Get-Date -Format s), $msg; Write-Host $line; Add-Content -Path $Log -Value $line }

function Invoke-KmsActivation {
    Log "Setting KMS server $Kms"
    cscript //nologo $slmgr /skms $Kms | Out-String | ForEach-Object { Add-Content -Path $Log -Value $_ }
    for ($i = 1; $i -le 20; $i++) {
        cscript //nologo $slmgr /ato | Out-String | ForEach-Object { Add-Content -Path $Log -Value $_ }
        $lic = Get-CimInstance SoftwareLicensingProduct -Filter "ApplicationID='$WinApp' AND PartialProductKey IS NOT NULL" | Select-Object -First 1
        if ($lic -and $lic.LicenseStatus -eq 1) { Log "Activated: $($lic.Name)"; return $true }
        Log "Not activated yet (attempt $i of 20), trying again in 30 seconds"
        Start-Sleep -Seconds 30
    }
    Log "Activation failed. Check that this server can reach $Kms (error 0xC004F074 = not reachable)."
    return $false
}

$cv      = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
$edition = $cv.EditionID
$build   = [int]$cv.CurrentBuildNumber

# Stage 2: runs once at startup after the edition change, then removes its own task
if ($Finish) {
    Log "After restart: edition $edition, build $build"
    $ok = Invoke-KmsActivation
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Log "Startup task removed"
    if ($ok) { exit 0 } else { exit 1 }
}

Log "Edition $edition, build $build, KMS server $Kms"

if (-not (Test-NetConnection -ComputerName $KmsHost -Port $KmsPort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Log "Cannot reach $Kms. Check the network or firewall. Nothing changed."
    exit 1
}

$os = Get-CimInstance Win32_OperatingSystem
if ($edition -like "*Eval*" -and $os.ProductType -eq 1) {
    Log "This is Windows $edition (a client Evaluation). It cannot be converted with DISM; install from volume license media instead. Nothing changed."
    exit 1
}

if (-not $keys.ContainsKey($build)) {
    Log "Windows build $build is not supported by this script (Windows Server 2016 to 2025). Nothing changed."
    exit 1
}

if ($edition -notlike "*Eval") {
    # Already a full edition: install the matching volume key when known, then activate
    if ($keys[$build].ContainsKey($edition)) {
        Log "Already a full edition. Installing the volume key for $edition"
        cscript //nologo $slmgr /ipk $keys[$build][$edition] | Out-String | ForEach-Object { Add-Content -Path $Log -Value $_ }
    } else {
        Log "Already a full edition ($edition). Keeping the current key."
    }
    if (Invoke-KmsActivation) { exit 0 } else { exit 1 }
}

if ((Get-CimInstance Win32_ComputerSystem).DomainRole -ge 4) {
    Log "This server is a domain controller. Microsoft does not support converting an Evaluation domain controller. Demote it first. Nothing changed."
    exit 1
}

$newEdition = if ($Target -eq "auto") { $edition -replace "Eval$", "" } else { $Target }
if (-not $keys[$build].ContainsKey($newEdition)) {
    Log "No volume key for $newEdition on build $build. Nothing changed."
    exit 1
}
$targets = (dism.exe /Online /Get-TargetEditions /English | Select-String "Target Edition : (\S+)" | ForEach-Object { $_.Matches[0].Groups[1].Value })
if ($targets -notcontains $newEdition) {
    Log "DISM cannot convert $edition to $newEdition here. Possible targets: $($targets -join ', '). Nothing changed."
    exit 1
}

# Copy this script so the startup task can run stage 2 after the restart
$self = "$Dir\convert-eval-and-activate.ps1"
if ($PSCommandPath -and ($PSCommandPath -ne $self)) { Copy-Item -Path $PSCommandPath -Destination $self -Force }
$action  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$self`" -Finish"
$trigger = New-ScheduledTaskTrigger -AtStartup
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -User "SYSTEM" -RunLevel Highest -Force | Out-Null
Log "Startup task '$TaskName' created"

Log "Converting $edition to $newEdition with DISM. This can take 10 to 20 minutes, do not turn off the server."
$key = $keys[$build][$newEdition]
dism.exe /Online "/Set-Edition:$newEdition" "/ProductKey:$key" /AcceptEula /NoRestart
if ($LASTEXITCODE -notin 0, 3010) {
    Log "DISM failed with exit code $LASTEXITCODE. Removing the startup task. See C:\Windows\Logs\DISM\dism.log"
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    exit 1
}
Log "Edition changed to $newEdition. KMS activation runs automatically after the restart."

if ($NoRestart) {
    Log "Restart the server yourself to finish. The log is $Log"
} else {
    Log "Restarting in 60 seconds (shutdown /a cancels)"
    shutdown.exe /r /t 60 /c "KMS Console: edition converted, restarting to finish the KMS activation"
}
"""

_EVAL_CMD = r"""@echo off
setlocal EnableExtensions EnableDelayedExpansion
:: KMS Console - convert Windows Server Evaluation to the full edition and activate
:: Server: __HP__
:: Run as administrator. Add the argument norestart to restart later yourself.
set "KMS=__HP__"
set "TARGET=__TARGET__"
set "TASK=__TASK__"
set "DIR=%ProgramData%\KMSConsole"
set "LOG=%DIR%\activation.log"
set "SLMGR=%windir%\System32\slmgr.vbs"
if not exist "%DIR%" mkdir "%DIR%"

net session >nul 2>&1 || (echo Please run this script as administrator. & pause & exit /b 1)

for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v EditionID ^| find "EditionID"') do set "EDITION=%%B"
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v CurrentBuildNumber ^| find "CurrentBuildNumber"') do set "BUILD=%%B"

if /i "%~1"=="finish" goto finish

call :log "Edition %EDITION%, build %BUILD%, KMS server %KMS%"

reg query "HKLM\SYSTEM\CurrentControlSet\Control\ProductOptions" /v ProductType | find /i "WinNT" >nul && (
  call :log "This script is for Windows Server. A Windows 10/11 Evaluation cannot be converted with DISM, and a full Windows 10/11 can use the Windows tab. Nothing changed."
  pause & exit /b 1
)

set "NEWED=%EDITION%"
if /i "%EDITION:~-4%"=="Eval" set "NEWED=%EDITION:~0,-4%"
if /i not "%TARGET%"=="auto" if /i "%EDITION:~-4%"=="Eval" set "NEWED=%TARGET%"

set "KEY="
__KEYS__
if not defined KEY (
  call :log "No volume key for %NEWED% on build %BUILD% (supported: Windows Server 2016 to 2025 Standard and Datacenter). Nothing changed."
  pause & exit /b 1
)

if /i not "%EDITION:~-4%"=="Eval" (
  call :log "Already a full edition. Installing the volume key and activating."
  cscript //nologo "%SLMGR%" /ipk %KEY% >>"%LOG%"
  goto finish
)

reg query "HKLM\SYSTEM\CurrentControlSet\Services\NTDS\Parameters" >nul 2>&1 && (
  call :log "This server is a domain controller. Microsoft does not support converting an Evaluation domain controller. Demote it first. Nothing changed."
  pause & exit /b 1
)

dism.exe /Online /Get-TargetEditions /English | find /i "Target Edition : %NEWED%" >nul || (
  call :log "DISM cannot convert %EDITION% to %NEWED% here. Run dism /online /get-targeteditions to see the options. Nothing changed."
  pause & exit /b 1
)

copy /y "%~f0" "%DIR%\convert-eval-and-activate.cmd" >nul
schtasks /create /tn "%TASK%" /sc onstart /ru SYSTEM /rl HIGHEST /f /tr "\"%DIR%\convert-eval-and-activate.cmd\" finish" >nul
call :log "Startup task '%TASK%' created"

call :log "Converting %EDITION% to %NEWED% with DISM. This can take 10 to 20 minutes, do not turn off the server."
dism.exe /Online /Set-Edition:%NEWED% /ProductKey:%KEY% /AcceptEula /NoRestart
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not "%RC%"=="3010" (
  call :log "DISM failed with exit code %RC%. Removing the startup task. See C:\Windows\Logs\DISM\dism.log"
  schtasks /delete /tn "%TASK%" /f >nul
  pause & exit /b 1
)
call :log "Edition changed to %NEWED%. KMS activation runs automatically after the restart."
if /i "%~1"=="norestart" (
  call :log "Restart the server yourself to finish. The log is %LOG%"
  pause & exit /b 0
)
call :log "Restarting in 60 seconds (shutdown /a cancels)"
shutdown.exe /r /t 60 /c "KMS Console: edition converted, restarting to finish the KMS activation"
exit /b 0

:finish
call :log "Activating: edition %EDITION%, build %BUILD%"
cscript //nologo "%SLMGR%" /skms %KMS% >>"%LOG%"
for /l %%I in (1,1,20) do (
  cscript //nologo "%SLMGR%" /ato >>"%LOG%"
  powershell -NoProfile -Command "$p = Get-CimInstance SoftwareLicensingProduct | Where-Object { $_.ApplicationID -eq '55c92734-d682-4d71-983e-d6ec3f16059f' -and $_.PartialProductKey -and $_.LicenseStatus -eq 1 }; if ($p) { exit 0 } else { exit 1 }"
  if !ERRORLEVEL!==0 (
    call :log "Activated"
    goto done
  )
  call :log "Not activated yet (attempt %%I of 20), trying again in 30 seconds"
  timeout /t 30 /nobreak >nul
)
call :log "Activation failed. Check that this server can reach %KMS% (error 0xC004F074 = not reachable)."
:done
schtasks /delete /tn "%TASK%" /f >nul 2>&1
exit /b 0

:log
echo [%date% %time%] %~1
>>"%LOG%" echo [%date% %time%] %~1
exit /b 0
"""


def _eval_script(kind, host, port, hp, target):
    if target not in EVAL_TARGETS:
        raise ValueError("invalid target edition")
    if kind == "eval-ps1":
        keys = "\n".join(
            f'    {build} = @{{ ServerStandard = "{k["ServerStandard"]}"; ServerDatacenter = "{k["ServerDatacenter"]}" }}   # {name}'
            for build, (name, k) in SERVER_GVLK.items())
        tpl = _EVAL_PS1
    else:
        keys = "\n".join(
            f'if "%BUILD%_%NEWED%"=="{build}_{ed}" set "KEY={key}"'
            for build, (_, k) in SERVER_GVLK.items() for ed, key in k.items())
        tpl = _EVAL_CMD
    out = (tpl.replace("__KEYS__", keys).replace("__HOST__", host).replace("__PORT__", str(port))
           .replace("__HP__", hp).replace("__TARGET__", target).replace("__TASK__", EVAL_TASK))
    return crlf(out)


def safe_host(host):
    host = (host or "").strip()
    if not _HOST_RE.match(host):
        raise ValueError("invalid host")
    return host


def safe_key(key):
    key = (key or "").strip().upper()
    if key and not _KEY_RE.match(key):
        raise ValueError("invalid key")
    return key


def _hostport(host, port):
    # IPv6 literals need brackets for slmgr host:port
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def crlf(text):
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def generate(kind, host, port, key="", product="", office_dir="Office16", domain="lan", target="auto"):
    host = safe_host(host)
    port = int(port)
    key = safe_key(key)
    hp = _hostport(host, port)
    title = product or "selected product"

    if kind == "windows-cmd":
        ipk = f'cscript //nologo "%windir%\\system32\\slmgr.vbs" /ipk {key}\n' if key else ":: Keeping the current product key (no GVLK selected)\n"
        return crlf(f"""@echo off
:: KMS Console - Windows activation for {title}
:: Run as administrator. Server: {hp}
net session >nul 2>&1 || (echo Please run this script as administrator. & pause & exit /b 1)

{ipk}cscript //nologo "%windir%\\system32\\slmgr.vbs" /skms {hp}
cscript //nologo "%windir%\\system32\\slmgr.vbs" /ato
cscript //nologo "%windir%\\system32\\slmgr.vbs" /xpr
pause
""")

    if kind == "windows-ps1":
        ipk = f'cscript //nologo $slmgr /ipk "{key}"\n' if key else "# Keeping the current product key (no GVLK selected)\n"
        return crlf(f"""#Requires -RunAsAdministrator
# KMS Console - Windows activation for {title}
# Server: {hp}
$ErrorActionPreference = "Stop"
$slmgr = "$env:windir\\System32\\slmgr.vbs"

Write-Host "Checking connection to {host} on port {port}..."
if (-not (Test-NetConnection -ComputerName "{host}" -Port {port} -InformationLevel Quiet)) {{
    Write-Warning "Cannot reach the KMS server. Check the network or firewall."
    exit 1
}}

{ipk}cscript //nologo $slmgr /skms "{hp}"
cscript //nologo $slmgr /ato
cscript //nologo $slmgr /xpr
""")

    if kind in ("eval-ps1", "eval-cmd"):
        return _eval_script(kind, host, port, hp, target)

    if kind == "office-cmd":
        inpkey = f'cscript //nologo "%OSPP%" /inpkey:{key}\n' if key else ":: Keeping the current Office key (no GVLK selected)\n"
        return crlf(f"""@echo off
:: KMS Console - Office activation for {title}
:: Run as administrator. Server: {hp}
net session >nul 2>&1 || (echo Please run this script as administrator. & pause & exit /b 1)

set "OSPP="
for %%D in ("%ProgramFiles%\\Microsoft Office\\{office_dir}" "%ProgramFiles(x86)%\\Microsoft Office\\{office_dir}") do (
  if exist "%%~D\\OSPP.VBS" set "OSPP=%%~D\\OSPP.VBS"
)
if not defined OSPP (echo OSPP.VBS not found. Is a volume license edition of Office installed? & pause & exit /b 1)

{inpkey}cscript //nologo "%OSPP%" /sethst:{host}
cscript //nologo "%OSPP%" /setprt:{port}
cscript //nologo "%OSPP%" /act
cscript //nologo "%OSPP%" /dstatus
pause
""")

    if kind == "office-ps1":
        inpkey = f'cscript //nologo $ospp /inpkey:{key}\n' if key else "# Keeping the current Office key (no GVLK selected)\n"
        return crlf(f"""#Requires -RunAsAdministrator
# KMS Console - Office activation for {title}
# Server: {hp}
$ErrorActionPreference = "Stop"
$candidates = @(
    "$env:ProgramFiles\\Microsoft Office\\{office_dir}\\OSPP.VBS",
    "${{env:ProgramFiles(x86)}}\\Microsoft Office\\{office_dir}\\OSPP.VBS"
)
$ospp = $candidates | Where-Object {{ Test-Path $_ }} | Select-Object -First 1
if (-not $ospp) {{ Write-Error "OSPP.VBS not found. Is a volume license edition of Office installed?"; exit 1 }}

{inpkey}cscript //nologo $ospp /sethst:{host}
cscript //nologo $ospp /setprt:{port}
cscript //nologo $ospp /act
cscript //nologo $ospp /dstatus
""")

    if kind == "reg":
        return crlf(f"""Windows Registry Editor Version 5.00

; KMS Console - point Windows and Office to {hp}
[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SoftwareProtectionPlatform]
"KeyManagementServiceName"="{host}"
"KeyManagementServicePort"="{port}"

[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\OfficeSoftwareProtectionPlatform]
"KeyManagementServiceName"="{host}"
"KeyManagementServicePort"="{port}"
""")

    if kind == "gpo":
        return crlf(f"""# KMS Console - create a Group Policy that points all domain PCs to {hp}
# Run on a domain controller (GroupPolicy module required).
Import-Module GroupPolicy
$gpo = New-GPO -Name "KMS Console - {host}" -Comment "KMS server {hp}"
$win = "HKLM\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\SoftwareProtectionPlatform"
$off = "HKLM\\SOFTWARE\\Microsoft\\OfficeSoftwareProtectionPlatform"
foreach ($k in @($win, $off)) {{
    Set-GPRegistryValue -Name $gpo.DisplayName -Key $k -ValueName "KeyManagementServiceName" -Type String -Value "{host}"
    Set-GPRegistryValue -Name $gpo.DisplayName -Key $k -ValueName "KeyManagementServicePort" -Type String -Value "{port}"
}}
# Link it to your domain or an OU:
# New-GPLink -Name $gpo.DisplayName -Target "DC=example,DC=local"
""")

    if kind == "dns":
        is_ip = bool(re.match(r"^[0-9.]+$", host)) or ":" in host
        target = f"kms.{domain}" if is_ip else host
        a_record = (f"{target}.  3600 IN {'AAAA' if ':' in host else 'A'}  {host}\n" if is_ip else "")
        a_note = (f"; An SRV record must point to a hostname, so also create {target} -> {host}\n" if is_ip else "")
        return f"""; KMS Console - DNS auto discovery
; Clients without a configured KMS server look up _vlmcs._tcp in their DNS suffix ({domain}).
{a_note}
; BIND / Pi-hole / pfSense / OPNsense
_vlmcs._tcp.{domain}.  3600 IN SRV 0 0 {port} {target}.
{a_record}
; dnsmasq one-liner
srv-host=_vlmcs._tcp.{domain},{target},{port},0,0

; UniFi Gateway (Network 8.2+): Settings > Policy Table > Create New Policy > DNS, type Service (SRV)
;   Name: _vlmcs._tcp.{domain}   Target: {target}   Port: {port}   Priority: 0   Weight: 0

# Windows DNS server (PowerShell)
Add-DnsServerResourceRecord -ZoneName "{domain}" -Srv -Name "_vlmcs._tcp" -DomainName "{target}" -Priority 0 -Weight 0 -Port {port}
"""

    if kind == "status":
        return crlf(f"""@echo off
:: KMS Console - show activation status
cscript //nologo "%windir%\\system32\\slmgr.vbs" /dlv
for %%D in ("%ProgramFiles%\\Microsoft Office\\Office16" "%ProgramFiles(x86)%\\Microsoft Office\\Office16" "%ProgramFiles%\\Microsoft Office\\Office15" "%ProgramFiles(x86)%\\Microsoft Office\\Office15") do (
  if exist "%%~D\\OSPP.VBS" cscript //nologo "%%~D\\OSPP.VBS" /dstatus
)
echo Expected KMS server: {hp}
pause
""")

    raise ValueError("unknown script type")
