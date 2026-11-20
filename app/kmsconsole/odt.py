"""Office install with the Office Deployment Tool (ODT), activated against this KMS server.

Generates a PowerShell script that downloads ODT from the Microsoft CDN, writes configuration.xml with
the public KMS client key (GVLK) of a volume license Office edition, installs it and activates with OSPP.VBS.
"""
from xml.sax.saxutils import quoteattr

# Office editions that ODT can install as a volume license product: id -> (name, GVLK, update channel, version).
# Keys are Microsoft's public KMS client keys (also in py-kms' KmsDataBase.xml).
EDITIONS = {
    "ProPlus2024Volume": ("Office LTSC Professional Plus 2024", "XJ2XN-FW8RK-P4HMP-DKDBV-GCVGB", "PerpetualVL2024", "2024"),
    "Standard2024Volume": ("Office LTSC Standard 2024", "V28N4-JG22K-W66P8-VTMGK-H6HGR", "PerpetualVL2024", "2024"),
    "ProPlus2021Volume": ("Office LTSC Professional Plus 2021", "FXYTK-NJJ8C-GB6DW-3DYQT-6F7TH", "PerpetualVL2021", "2021"),
    "Standard2021Volume": ("Office LTSC Standard 2021", "KDX7X-BNVR8-TXXGX-4Q7Y8-78VT3", "PerpetualVL2021", "2021"),
    "ProPlus2019Volume": ("Office Professional Plus 2019", "NMMKJ-6RK4F-KMJVX-8D9MJ-6MWKP", "PerpetualVL2019", "2019"),
    "Standard2019Volume": ("Office Standard 2019", "6NWWJ-YQWMR-QKGCB-6TMB3-9D9HK", "PerpetualVL2019", "2019"),
}
DEFAULT_EDITION = "ProPlus2024Volume"

# Visio and Project Professional per Office version: id -> (name, GVLK)
EXTRAS = {
    "2024": {"visio": ("VisioPro2024Volume", "Visio LTSC Professional 2024", "B7TN8-FJ8V3-7QYCP-HQPMV-YY89G"),
             "project": ("ProjectPro2024Volume", "Project Professional 2024", "FQQ23-N4YCY-73HQ3-FM9WC-76HF4")},
    "2021": {"visio": ("VisioPro2021Volume", "Visio LTSC Professional 2021", "KNH8D-FGHT4-T8RK3-CTDYJ-K2HT4"),
             "project": ("ProjectPro2021Volume", "Project Professional 2021", "FTNWT-C6WBT-8HMGF-K9PRX-QV9H8")},
    "2019": {"visio": ("VisioPro2019Volume", "Visio Professional 2019", "9BGNQ-K37YR-RQHF2-38RQ3-7VCBB"),
             "project": ("ProjectPro2019Volume", "Project Professional 2019", "B4NPR-3FKK7-T2MBV-FRQ4W-PKD2B")},
}

# ODT app ids that can be left out. Publisher is not part of LTSC 2024.
APPS = ["Word", "Excel", "PowerPoint", "Outlook", "OneNote", "Access", "Publisher", "Lync", "OneDrive"]
APP_LABELS = {"Lync": "Skype for Business"}
DEFAULT_APPS = ["Word", "Excel", "PowerPoint", "Outlook", "OneNote"]

LANGUAGES = {
    "MatchOS": "Same as Windows (English as fallback)",
    "en-us": "English (en-us)",
    "nl-nl": "Nederlands (nl-nl)",
    "en-us,nl-nl": "English + Nederlands",
}
ARCHS = ("64", "32")

SETUP_URL = "https://officecdn.microsoft.com/pr/wsus/setup.exe"


def apps_for(edition):
    version = EDITIONS[edition][3]
    return [a for a in APPS if not (a == "Publisher" and version == "2024")]


def options(edition=DEFAULT_EDITION, language="MatchOS", arch="64", apps=None, visio=False, project=False, remove=True):
    """Validate the choices from the GUI and return them as a clean dict."""
    if edition not in EDITIONS:
        edition = DEFAULT_EDITION
    if language not in LANGUAGES:
        language = "MatchOS"
    if arch not in ARCHS:
        arch = "64"
    allowed = apps_for(edition)
    apps = [a for a in (DEFAULT_APPS if apps is None else apps) if a in allowed]
    if not apps:
        apps = [a for a in DEFAULT_APPS if a in allowed]
    return {"edition": edition, "language": language, "arch": arch, "apps": apps,
            "visio": bool(visio), "project": bool(project), "remove": bool(remove)}


def products(opts):
    """[(product id, name, key)] that the configuration installs."""
    name, key, _, version = EDITIONS[opts["edition"]]
    out = [(opts["edition"], name, key)]
    for extra in ("visio", "project"):
        if opts[extra]:
            out.append(EXTRAS[version][extra])
    return out


def configuration_xml(opts, source_path=""):
    """The ODT configuration.xml for these options (the PowerShell script builds the same file itself)."""
    _, _, channel, _ = EDITIONS[opts["edition"]]
    excluded = [a for a in apps_for(opts["edition"]) if a not in opts["apps"]] + ["Groove"]
    langs = opts["language"].split(",")
    lines = ["<Configuration>"]
    src = f" SourcePath={quoteattr(source_path)}" if source_path else ""
    lines.append(f'  <Add OfficeClientEdition="{opts["arch"]}" Channel="{channel}"{src} AllowCdnFallback="TRUE">')
    for i, (pid, _, key) in enumerate(products(opts)):
        lines.append(f'    <Product ID="{pid}" PIDKEY="{key}">')
        for lang in langs:
            fallback = ' Fallback="en-us"' if lang == "MatchOS" else ""
            lines.append(f'      <Language ID="{lang}"{fallback} />')
        # Visio and Project only bring their own app; leave out the shared OneDrive and Groove there too
        for app in (excluded if i == 0 else [a for a in ("OneDrive", "Groove") if a in excluded]):
            lines.append(f'      <ExcludeApp ID="{app}" />')
        lines.append("    </Product>")
    lines.append("  </Add>")
    lines.append(f'  <Updates Enabled="TRUE" Channel="{channel}" />')
    lines.append("  <RemoveMSI />")
    lines.append('  <Property Name="FORCEAPPSHUTDOWN" Value="TRUE" />')
    lines.append('  <Property Name="SharedComputerLicensing" Value="0" />')
    lines.append('  <Display Level="None" AcceptEULA="TRUE" />')
    lines.append('  <Logging Level="Standard" Path="%ProgramData%\\KMSConsole\\Office\\logs" />')
    lines.append("</Configuration>")
    return "\n".join(lines) + "\n"


_PS1 = r"""#Requires -RunAsAdministrator
# KMS Console - install __NAME__ with the Office Deployment Tool and activate
# Server: __HP__
# Run as administrator on the client. Every choice below can be changed with a parameter, for example:
#   .\install-office.ps1 -Edition Standard2021Volume -Language nl-nl -Arch 32 -Visio
param(
    [ValidateSet(__EDITION_SET__)]
    [string]  $Edition  = "__EDITION__",
    [ValidateSet("MatchOS", "en-us", "nl-nl", "en-us,nl-nl")]
    [string]  $Language = "__LANGUAGE__",
    [ValidateSet("64", "32")]
    [string]  $Arch     = "__ARCH__",
    [string[]]$Apps     = @(__APPS__),
    [switch]  $Visio    = $__VISIO__,
    [switch]  $Project  = $__PROJECT__,
    [switch]  $NoRemove = $__NOREMOVE__,   # keep other Office installs (Microsoft 365, retail, MSI)
    [string]  $KmsHost  = "__HOST__",
    [int]     $KmsPort  = __PORT__,
    [string]  $SetupPath,                  # your own ODT setup.exe, for example on a file share
    [string]  $SourcePath                  # folder with Office files from setup.exe /download (offline install)
)
$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # makes Invoke-WebRequest much faster

$Dir = "$env:ProgramData\KMSConsole\Office"
$Log = "$env:ProgramData\KMSConsole\office-install.log"
New-Item -ItemType Directory -Force -Path "$Dir\logs" | Out-Null
function Log($msg) { $line = "[{0}] {1}" -f (Get-Date -Format s), $msg; Write-Host $line; Add-Content -Path $Log -Value $line }

# Volume license editions: id = name, KMS client key (GVLK), update channel, version
$Editions = @{
__EDITIONS__
}
$Extras = @{
__EXTRAS__
}
$AllApps = @("Word", "Excel", "PowerPoint", "Outlook", "OneNote", "Access", "Publisher", "Lync", "OneDrive")

$ed = $Editions[$Edition]
$name, $channel, $version = $ed.Name, $ed.Channel, $ed.Version
$Apps = @($Apps | ForEach-Object { $_ -split "," } | ForEach-Object { $_.Trim() } | Where-Object { $_ })   # -Apps "Word,Excel" from cmd
$products = @(@{ Id = $Edition; Key = $ed.Key; Name = $name })
if ($Visio)   { $products += $Extras[$version].Visio }
if ($Project) { $products += $Extras[$version].Project }
$Kms = if ($KmsHost -match ":" -and $KmsHost -notmatch "^\[") { "[$KmsHost]:$KmsPort" } else { "${KmsHost}:$KmsPort" }
Log "Installing $name ($Arch-bit, language $Language) and activating against $Kms"

# 0. Checks
if (-not (Test-NetConnection -ComputerName $KmsHost -Port $KmsPort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Log "Warning: cannot reach $Kms now. Office is installed anyway; activation is tried at the end."
}
if ($Arch -eq "64" -and -not [Environment]::Is64BitOperatingSystem) {
    Log "This is 32-bit Windows, switching to 32-bit Office."
    $Arch = "32"
}

# 1. The Office Deployment Tool
$setup = "$Dir\setup.exe"
if ($SetupPath) {
    Copy-Item -Path $SetupPath -Destination $setup -Force
    Log "Using ODT from $SetupPath"
} else {
    Log "Downloading the Office Deployment Tool from __SETUP_URL__"
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri "__SETUP_URL__" -OutFile $setup -UseBasicParsing
}
$sig = Get-AuthenticodeSignature -FilePath $setup
if ($sig.Status -ne "Valid" -or $sig.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
    Log "setup.exe is not signed by Microsoft (status $($sig.Status)). Stopping, nothing installed."
    exit 1
}

# 2. Remove other Office installs first (Click-to-Run). Old MSI versions go with RemoveMSI below.
$c2r = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration" -ErrorAction SilentlyContinue
$installed = if ($c2r -and $c2r.ProductReleaseIds) { @($c2r.ProductReleaseIds -split ",") } else { @() }
$others = @($installed | Where-Object { $_ -notin $products.Id })
if ($installed.Count -and -not $others.Count -and $c2r.Platform -eq $(if ($Arch -eq "64") { "x64" } else { "x86" })) {
    Log "Already installed: $($c2r.ProductReleaseIds). Nothing to remove."
} elseif (-not $NoRemove -and $installed.Count) {
    Log "Removing installed Office: $($c2r.ProductReleaseIds)"
    $remove = "$Dir\remove.xml"
    Set-Content -Path $remove -Encoding UTF8 -Value @"
<Configuration>
  <Remove All="TRUE" />
  <Property Name="FORCEAPPSHUTDOWN" Value="TRUE" />
  <Display Level="None" AcceptEULA="TRUE" />
  <Logging Level="Standard" Path="$Dir\logs" />
</Configuration>
"@
    $p = Start-Process -FilePath $setup -ArgumentList "/configure `"$remove`"" -Wait -PassThru -NoNewWindow
    if ($p.ExitCode -ne 0) { Log "Removing Office failed (exit code $($p.ExitCode)). See $Dir\logs"; exit 1 }
    Log "Old Office removed"
} elseif ($installed.Count) {
    Log "Keeping installed Office ($($c2r.ProductReleaseIds)). ODT stops if it conflicts with the new version."
}

# 3. configuration.xml with the KMS client keys
$excluded = @($AllApps | Where-Object { $_ -notin $Apps -and -not ($_ -eq "Publisher" -and $version -eq "2024") }) + "Groove"
$langs = $Language -split ","
$src = if ($SourcePath) { " SourcePath=`"$SourcePath`"" } else { "" }

$xml = @("<Configuration>", "  <Add OfficeClientEdition=`"$Arch`" Channel=`"$channel`"$src AllowCdnFallback=`"TRUE`">")
$first = $true
foreach ($prod in $products) {
    $xml += "    <Product ID=`"$($prod.Id)`" PIDKEY=`"$($prod.Key)`">"
    foreach ($l in $langs) {
        if ($l -eq "MatchOS") { $xml += "      <Language ID=`"MatchOS`" Fallback=`"en-us`" />" } else { $xml += "      <Language ID=`"$l`" />" }
    }
    $skip = if ($first) { $excluded } else { $excluded | Where-Object { $_ -in "OneDrive", "Groove" } }
    foreach ($a in $skip) { $xml += "      <ExcludeApp ID=`"$a`" />" }
    $xml += "    </Product>"
    $first = $false
}
$xml += @(
    "  </Add>",
    "  <Updates Enabled=`"TRUE`" Channel=`"$channel`" />",
    "  <RemoveMSI />",
    "  <Property Name=`"FORCEAPPSHUTDOWN`" Value=`"TRUE`" />",
    "  <Property Name=`"SharedComputerLicensing`" Value=`"0`" />",
    "  <Display Level=`"None`" AcceptEULA=`"TRUE`" />",
    "  <Logging Level=`"Standard`" Path=`"$Dir\logs`" />",
    "</Configuration>"
)
$config = "$Dir\configuration.xml"
Set-Content -Path $config -Value $xml -Encoding UTF8
Log "Wrote $config ($(($products | ForEach-Object { $_.Name }) -join ', '))"

# 4. Install. ODT downloads Office from the Microsoft CDN (about 3 GB) unless SourcePath is set.
Log "Installing Office. This takes 5 to 15 minutes, keep the PC on."
$p = Start-Process -FilePath $setup -ArgumentList "/configure `"$config`"" -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) {
    Log "Office setup failed with exit code $($p.ExitCode). See the ODT logs in $Dir\logs"
    exit 1
}
Log "Office installed"

# 5. Activate with KMS
$ospp = @("$env:ProgramFiles\Microsoft Office\Office16\OSPP.VBS", "${env:ProgramFiles(x86)}\Microsoft Office\Office16\OSPP.VBS") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ospp) { Log "OSPP.VBS not found after the install. Check $Dir\logs"; exit 1 }

function Ospp([string]$arg) { $out = cscript //nologo $ospp $arg | Out-String; Add-Content -Path $Log -Value $out; return $out }
foreach ($prod in $products) { Ospp "/inpkey:$($prod.Key)" | Out-Null }
Ospp "/sethst:$KmsHost" | Out-Null
Ospp "/setprt:$KmsPort" | Out-Null
for ($i = 1; $i -le 10; $i++) {
    Ospp "/act" | Out-Null
    $status = Ospp "/dstatus"
    $licensed = ([regex]::Matches($status, "LICENSE STATUS:\s+---LICENSED---")).Count
    if ($licensed -ge $products.Count) {
        Log "Activated: $(($products | ForEach-Object { $_.Name }) -join ', ')"
        Write-Host $status
        exit 0
    }
    Log "Not activated yet (attempt $i of 10), trying again in 30 seconds"
    Start-Sleep -Seconds 30
}
Log "Office is installed but not activated. Check that this PC can reach $Kms (error 0xC004F074 = not reachable). Run the Office tab script later to try again."
exit 1
"""


def _ps_bool(value):
    return "true" if value else "false"


def install_script(opts, host, hp, port):
    editions = "\n".join(
        f'    "{pid}" = @{{ Name = "{name}"; Key = "{key}"; Channel = "{channel}"; Version = "{version}" }}'
        for pid, (name, key, channel, version) in EDITIONS.items())
    extras = "\n".join(
        f'    "{version}" = @{{ Visio = @{{ Id = "{e["visio"][0]}"; Name = "{e["visio"][1]}"; Key = "{e["visio"][2]}" }};'
        f' Project = @{{ Id = "{e["project"][0]}"; Name = "{e["project"][1]}"; Key = "{e["project"][2]}" }} }}'
        for version, e in EXTRAS.items())
    values = {
        "__NAME__": EDITIONS[opts["edition"]][0],
        "__EDITION_SET__": ", ".join(f'"{e}"' for e in EDITIONS),
        "__EDITION__": opts["edition"],
        "__LANGUAGE__": opts["language"],
        "__ARCH__": opts["arch"],
        "__APPS__": ", ".join(f'"{a}"' for a in opts["apps"]),
        "__VISIO__": _ps_bool(opts["visio"]),
        "__PROJECT__": _ps_bool(opts["project"]),
        "__NOREMOVE__": _ps_bool(not opts["remove"]),
        "__EDITIONS__": editions,
        "__EXTRAS__": extras,
        "__SETUP_URL__": SETUP_URL,
        "__HOST__": host,
        "__PORT__": str(port),
        "__HP__": hp,
    }
    out = _PS1
    for k, v in values.items():
        out = out.replace(k, v)
    return out
