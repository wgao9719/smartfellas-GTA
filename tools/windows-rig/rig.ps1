# In-game QA helpers for the Living Los Santos G0 checks on this PC.
# Dot-source:  . .\tools\windows-rig\rig.ps1
. (Join-Path $PSScriptRoot 'rig-paths.ps1')
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RigInput {
    [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Explicit)] public struct INPUTUNION { [FieldOffset(0)] public MOUSEINPUT mi; [FieldOffset(0)] public KEYBDINPUT ki; }
    [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public INPUTUNION u; }
    [DllImport("user32.dll", SetLastError = true)] public static extern uint SendInput(uint n, INPUT[] inputs, int size);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    const uint KEYEVENTF_EXTENDEDKEY = 0x1, KEYEVENTF_KEYUP = 0x2, KEYEVENTF_SCANCODE = 0x8;
    public static void Key(ushort scan, bool extended, bool up) {
        var i = new INPUT { type = 1 };
        i.u.ki.wScan = scan;
        i.u.ki.dwFlags = KEYEVENTF_SCANCODE | (extended ? KEYEVENTF_EXTENDEDKEY : 0) | (up ? KEYEVENTF_KEYUP : 0);
        SendInput(1, new[] { i }, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void MouseButton(uint flags) {
        var i = new INPUT { type = 0 };
        i.u.mi.dwFlags = flags; // LEFTDOWN 0x2, LEFTUP 0x4, RIGHTDOWN 0x8, RIGHTUP 0x10
        SendInput(1, new[] { i }, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void Wheel(int delta) {
        var i = new INPUT { type = 0 };
        i.u.mi.dwFlags = 0x0800; i.u.mi.mouseData = unchecked((uint)delta); // MOUSEEVENTF_WHEEL, 120 per notch
        SendInput(1, new[] { i }, Marshal.SizeOf(typeof(INPUT)));
    }
    public static void MouseMove(int dx, int dy) {
        var i = new INPUT { type = 0 };
        i.u.mi.dx = dx; i.u.mi.dy = dy; i.u.mi.dwFlags = 0x1; // MOUSEEVENTF_MOVE (relative)
        SendInput(1, new[] { i }, Marshal.SizeOf(typeof(INPUT)));
    }
}
"@

# The display is 4K at 150% scaling; capture and coordinates must use physical pixels.
[RigInput]::SetProcessDPIAware() | Out-Null

# ffmpeg Desktop Duplication recording (works without admin). Start-RigRecording returns the process.
$script:Ffmpeg = Join-Path $script:RigTools 'ffmpeg\ffmpeg-master-latest-win64-gpl\bin\ffmpeg.exe'
function Start-RigRecording([string]$Path, [int]$Fps = 30, [int]$Width = 1920) {
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    $args = @('-hide_banner','-loglevel','error','-y','-f','lavfi','-i',"ddagrab=output_idx=0:framerate=$Fps",
              '-vf',"hwdownload,format=bgra,scale=${Width}:-2,format=yuv420p",'-c:v','h264_amf','-b:v','12M',$Path)
    $p = Start-Process -FilePath $script:Ffmpeg -ArgumentList $args -PassThru -WindowStyle Hidden -RedirectStandardInput "$env:TEMP\rig-ffmpeg-stdin.txt" -RedirectStandardError "$Path.log"
    $p
}
function Stop-RigRecording($Process) {
    # ffmpeg finalizes MP4 on 'q' via stdin; fall back to a polite stop.
    if ($Process -and -not $Process.HasExited) { Stop-Process -Id $Process.Id; Start-Sleep -Seconds 2 }
}

# Set-1 scan codes. [scan, extended]
$script:RigScan = @{
    'Esc'=@(0x01,$false); 'Back'=@(0x0E,$false); 'Tab'=@(0x0F,$false); 'Enter'=@(0x1C,$false); 'Space'=@(0x39,$false)
    'F1'=@(0x3B,$false); 'F2'=@(0x3C,$false); 'F3'=@(0x3D,$false); 'F4'=@(0x3E,$false); 'F5'=@(0x3F,$false); 'F6'=@(0x40,$false)
    'F7'=@(0x41,$false); 'F8'=@(0x42,$false); 'F9'=@(0x43,$false); 'F10'=@(0x44,$false); 'F11'=@(0x57,$false); 'F12'=@(0x58,$false)
    'LShift'=@(0x2A,$false); 'LCtrl'=@(0x1D,$false); 'LAlt'=@(0x38,$false)
    'Up'=@(0x48,$true); 'Down'=@(0x50,$true); 'Left'=@(0x4B,$true); 'Right'=@(0x4D,$true)
    '1'=@(0x02,$false); '2'=@(0x03,$false); '3'=@(0x04,$false); '4'=@(0x05,$false); '5'=@(0x06,$false); '6'=@(0x07,$false); '7'=@(0x08,$false); '8'=@(0x09,$false); '9'=@(0x0A,$false); '0'=@(0x0B,$false)
    'Q'=@(0x10,$false); 'W'=@(0x11,$false); 'E'=@(0x12,$false); 'R'=@(0x13,$false); 'T'=@(0x14,$false); 'Y'=@(0x15,$false); 'U'=@(0x16,$false); 'I'=@(0x17,$false); 'O'=@(0x18,$false); 'P'=@(0x19,$false)
    'A'=@(0x1E,$false); 'S'=@(0x1F,$false); 'D'=@(0x20,$false); 'F'=@(0x21,$false); 'G'=@(0x22,$false); 'H'=@(0x23,$false); 'J'=@(0x24,$false); 'K'=@(0x25,$false); 'L'=@(0x26,$false)
    'Z'=@(0x2C,$false); 'X'=@(0x2D,$false); 'C'=@(0x2E,$false); 'V'=@(0x2F,$false); 'B'=@(0x30,$false); 'N'=@(0x31,$false); 'M'=@(0x32,$false)
    ','=@(0x33,$false); '.'=@(0x34,$false); '['=@(0x1A,$false); ']'=@(0x1B,$false); '\'=@(0x2B,$false); '`'=@(0x29,$false); '/'=@(0x35,$false); "'"=@(0x28,$false); ';'=@(0x27,$false); '-'=@(0x0C,$false); '='=@(0x0D,$false)
}

function Send-RigKey([string]$Key, [int]$HoldMs = 60) {
    $s = $script:RigScan[$Key]; if (-not $s) { throw "unknown key $Key" }
    if ([RigInput]::GetForegroundWindow() -ne (Get-RigWindow)) { throw "GTA lost focus; input not sent." }
    [RigInput]::Key([uint16]$s[0], $s[1], $false); Start-Sleep -Milliseconds $HoldMs; [RigInput]::Key([uint16]$s[0], $s[1], $true)
}

function Hold-RigKeys([string[]]$Keys, [int]$Ms) {
    if ([RigInput]::GetForegroundWindow() -ne (Get-RigWindow)) { throw "GTA lost focus; input not sent." }
    foreach ($k in $Keys) { $s = $script:RigScan[$k]; [RigInput]::Key([uint16]$s[0], $s[1], $false) }
    Start-Sleep -Milliseconds $Ms
    foreach ($k in $Keys) { $s = $script:RigScan[$k]; [RigInput]::Key([uint16]$s[0], $s[1], $true) }
}
function Send-RigMouse([ValidateSet('Left','Right')][string]$Button = 'Left', [int]$HoldMs = 80) {
    if ([RigInput]::GetForegroundWindow() -ne (Get-RigWindow)) { throw "GTA lost focus; input not sent." }
    if ($Button -eq 'Left') { [RigInput]::MouseButton(0x2); Start-Sleep -Milliseconds $HoldMs; [RigInput]::MouseButton(0x4) }
    else { [RigInput]::MouseButton(0x8); Start-Sleep -Milliseconds $HoldMs; [RigInput]::MouseButton(0x10) }
}
function Click-RigAt([int]$X, [int]$Y, [ValidateSet('Left','Right')][string]$Button = 'Left') {
    # Absolute physical-pixel position on the 3840x2160 display.
    if ([RigInput]::GetForegroundWindow() -ne (Get-RigWindow)) { throw "GTA lost focus; input not sent." }
    [RigInput]::SetCursorPos($X, $Y) | Out-Null; Start-Sleep -Milliseconds 120
    Send-RigMouse $Button 70
}
function Move-RigMouse([int]$Dx, [int]$Dy, [int]$Steps = 10) {
    if ([RigInput]::GetForegroundWindow() -ne (Get-RigWindow)) { throw "GTA lost focus; input not sent." }
    for ($i = 0; $i -lt $Steps; $i++) { [RigInput]::MouseMove([int]($Dx / $Steps), [int]($Dy / $Steps)); Start-Sleep -Milliseconds 10 }
}
function Shot([string]$Name, [double]$Scale = 0.33) {
    Save-RigScreenshot (Join-Path $script:RigEvidence "screens\$Name.png") $Scale
}

function Send-RigText([string]$Text, [int]$GapMs = 30) {
    # US layout: character -> base key and whether Shift is held.
    $shiftMap = @{ '!'='1'; '@'='2'; '#'='3'; '$'='4'; '%'='5'; '^'='6'; '&'='7'; '*'='8'; '('='9'; ')'='0';
                   '_'='-'; '+'='='; '{'='['; '}'=']'; ':'=';'; '"'="'"; '<'=','; '>'='.'; '?'='/'; '|'='\' }
    if ([RigInput]::GetForegroundWindow() -ne (Get-RigWindow)) { throw "GTA lost focus; input not sent." }
    foreach ($ch in $Text.ToCharArray()) {
        $c = [string]$ch
        if ($c -eq ' ') { Send-RigKey 'Space' 25; Start-Sleep -Milliseconds $GapMs; continue }
        $shift = $false; $base = $c
        if ($shiftMap.ContainsKey($c)) { $shift = $true; $base = $shiftMap[$c] }
        elseif ($c -cmatch '[A-Z]') { $shift = $true }
        $base = $base.ToUpper()
        if (-not $script:RigScan.ContainsKey($base)) { throw "no key for character '$c'" }
        if ($shift) { [RigInput]::Key(0x2A, $false, $false) }
        Send-RigKey $base 25
        if ($shift) { [RigInput]::Key(0x2A, $false, $true) }
        Start-Sleep -Milliseconds $GapMs
    }
}

function Get-RigWindow([string]$ProcessName = 'GTA5') {
    $p = Get-Process $ProcessName -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0 | Select-Object -First 1
    if ($p) { $p.MainWindowHandle } else { [IntPtr]::Zero }
}
function Focus-Rig([string]$ProcessName = 'GTA5') {
    $h = Get-RigWindow $ProcessName
    if ($h -eq [IntPtr]::Zero) { return $false }
    for ($try = 0; $try -lt 5 -and [RigInput]::GetForegroundWindow() -ne $h; $try++) {
        $fg = [RigInput]::GetForegroundWindow(); $pid0 = 0
        $fgThread = [RigInput]::GetWindowThreadProcessId($fg, [ref]$pid0); $me = [RigInput]::GetCurrentThreadId()
        [RigInput]::AttachThreadInput($me, $fgThread, $true) | Out-Null
        # A synthetic Alt tap lets this process take the foreground (Windows foreground lock).
        [RigInput]::Key(0x38, $false, $false); [RigInput]::Key(0x38, $false, $true)
        [RigInput]::ShowWindow($h, 9) | Out-Null; [RigInput]::BringWindowToTop($h) | Out-Null; [RigInput]::SetForegroundWindow($h) | Out-Null
        [RigInput]::AttachThreadInput($me, $fgThread, $false) | Out-Null
        Start-Sleep -Milliseconds 400
    }
    return ([RigInput]::GetForegroundWindow() -eq $h)
}
function Assert-RigFocus {
    # Never send input to another window (e.g. the Claude chat box).
    if (-not (Focus-Rig)) { throw "GTA is not the foreground window; refusing to send input." }
}

function Save-RigScreenshot([string]$Path, [double]$Scale = 0.5, [switch]$AllScreens, [int]$Screen = -1) {
    # Default: the monitor showing the GTA window if it exists, else the primary monitor.
    $b = if ($AllScreens) { [System.Windows.Forms.SystemInformation]::VirtualScreen }
         elseif ($Screen -ge 0) { [System.Windows.Forms.Screen]::AllScreens[$Screen].Bounds }
         else {
             $p = Get-Process GTA5 -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0 | Select-Object -First 1
             if ($p) { [System.Windows.Forms.Screen]::FromHandle($p.MainWindowHandle).Bounds } else { [System.Windows.Forms.Screen]::PrimaryScreen.Bounds }
         }
    $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); $g.Dispose()
    $out = if ($Scale -ne 1) { New-Object System.Drawing.Bitmap $bmp, ([int]($b.Width * $Scale)), ([int]($b.Height * $Scale)) } else { $bmp }
    New-Item -ItemType Directory -Force (Split-Path $Path) | Out-Null
    $out.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose(); if ($out -ne $bmp) { $out.Dispose() }
    $Path
}

function Test-RigConsoleOpen {
    # SHVDN console: darkened log area (y≈700) above a blue input bar (y≈760) at 3840x2160.
    $bmp = New-Object System.Drawing.Bitmap 3840, 2160
    $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen(0, 0, 0, 0, $bmp.Size); $g.Dispose()
    $dark = 0; $blue = 0
    for ($x = 400; $x -le 3400; $x += 200) {
        $a = $bmp.GetPixel($x, 700); if (($a.R + $a.G + $a.B) / 3 -lt 70) { $dark++ }
        $b = $bmp.GetPixel($x, 760); if ($b.B - $b.R -ge 25 -and $b.B -ge 70 -and $b.B -le 140) { $blue++ }
    }
    $bmp.Dispose()
    # The darkened log area is unreliable in bright scenes; the input bar alone is distinctive.
    return ($blue -ge 13)
}
function Close-RigConsole {
    # The console animates; re-checking too soon sees the closing bar and toggles it open again.
    if (Test-RigConsoleOpen) { Send-RigKey 'F4'; Start-Sleep -Milliseconds 2000 }
    return -not (Test-RigConsoleOpen)
}
function Invoke-RigConsole([string]$Code, [int]$WaitMs = 1500) {
    # Open the SHVDN console, run one statement, then close it again (verified).
    if (-not (Test-RigConsoleOpen)) { Send-RigKey 'F4'; Start-Sleep -Milliseconds 1400 }
    Send-RigText $Code 35; Send-RigKey 'Enter'; Start-Sleep -Milliseconds $WaitMs
    if (-not (Close-RigConsole)) { throw 'SHVDN console did not close' }
}

function Get-RigGameRootSnapshot([string]$Root, [string]$OutFile) {
    # Top-level files and folders plus hashes of small top-level files; never descends into game archives.
    $rows = Get-ChildItem -LiteralPath $Root -Force | Sort-Object Name | ForEach-Object {
        $hash = if (-not $_.PSIsContainer -and $_.Length -lt 50MB) { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash } else { '' }
        [pscustomobject]@{ Name = $_.Name; Dir = $_.PSIsContainer; Length = if ($_.PSIsContainer) { '' } else { $_.Length }; SHA256 = $hash }
    }
    $rows | ConvertTo-Csv -NoTypeInformation | Set-Content -Encoding utf8 $OutFile
    $rows
}
