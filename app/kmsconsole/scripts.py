"""Generates client activation scripts that point to this KMS container."""
import re

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_KEY_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")

TYPES = ["windows-cmd", "windows-ps1", "office-cmd", "office-ps1", "reg", "gpo", "dns", "status"]

FILENAMES = {
    "windows-cmd": "activate-windows.cmd",
    "windows-ps1": "activate-windows.ps1",
    "office-cmd": "activate-office.cmd",
    "office-ps1": "activate-office.ps1",
    "reg": "kms-server.reg",
    "gpo": "kms-gpo.ps1",
    "dns": "kms-dns-srv.txt",
    "status": "kms-status.cmd",
}


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


def generate(kind, host, port, key="", product="", office_dir="Office16", domain="lan"):
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
