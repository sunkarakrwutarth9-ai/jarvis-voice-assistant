"""Puts an "Atomo" app icon on the Desktop (and in the Start menu).

    python build_exe.py          Desktop + Start-menu icon (works everywhere, incl. Smart App Control PCs)
    python build_exe.py --exe    also build a native Atomo.exe launcher (blocked on PCs with Smart App
                                 Control on, because it isn't signed by a trusted publisher)

Double-clicking the icon starts Atomo in the background (no console window); if Atomo is already
running it opens the command center instead.
"""

import math
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICON = HERE / "assets" / "atomo.ico"
EXE = HERE / "Atomo.exe"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

LAUNCHER = r'''
using System;
using System.Diagnostics;
using System.IO;
using System.Net;

[assembly: System.Reflection.AssemblyTitle("Atomo")]
[assembly: System.Reflection.AssemblyProduct("Atomo voice assistant")]
[assembly: System.Reflection.AssemblyVersion("1.0.0.0")]

class Atomo {
    const string Home = @"__HOME__";
    const string Python = @"__PYTHONW__";

    static bool Running() {
        try {
            var req = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:7777/api/state");
            req.Timeout = 1500;
            using (var resp = (HttpWebResponse)req.GetResponse()) return resp.StatusCode == HttpStatusCode.OK;
        } catch { return false; }
    }

    [STAThread]
    static void Main() {
        // the exe may live on the Desktop: find the Atomo folder next to it, else the one it was built in
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        if (!File.Exists(Path.Combine(dir, "jarvis.py"))) dir = Home;
        if (Running()) {                                   // already on: just show the command center
            Process.Start(new ProcessStartInfo("http://localhost:7777") { UseShellExecute = true });
            return;
        }
        string py = Path.Combine(dir, @".venv\Scripts\pythonw.exe");
        if (!File.Exists(py)) py = File.Exists(Python) ? Python : "pythonw";
        Process.Start(new ProcessStartInfo(py, "\"" + Path.Combine(dir, "jarvis.py") + "\"") {
            WorkingDirectory = dir, UseShellExecute = false, CreateNoWindow = true });
    }
}
'''


def make_icon():
    """Draw the atom (nucleus + three electron orbits) at several sizes."""
    from PIL import Image, ImageDraw, ImageFilter
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, S - 8, S - 8), fill=(6, 10, 22, 255), outline=(244, 186, 66, 255), width=6)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((78, 78, S - 78, S - 78), fill=(127, 230, 255, 200))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(18)))
    c, colors = S / 2, [(127, 230, 255), (244, 186, 66), (255, 77, 90)]
    for k, col in enumerate(colors):
        ang = math.radians(k * 60)
        pts = []
        for i in range(181):
            t = i / 180 * 2 * math.pi
            x, y = math.cos(t) * 96, math.sin(t) * 36
            pts.append((c + x * math.cos(ang) - y * math.sin(ang), c + x * math.sin(ang) + y * math.cos(ang)))
        d.line(pts, fill=col + (255,), width=7, joint="curve")
        t = 0.9 + k * 2.1                                   # an electron on each orbit
        x, y = math.cos(t) * 96, math.sin(t) * 36
        ex, ey = c + x * math.cos(ang) - y * math.sin(ang), c + x * math.sin(ang) + y * math.cos(ang)
        d.ellipse((ex - 12, ey - 12, ex + 12, ey + 12), fill=(255, 255, 255, 255), outline=col + (255,), width=4)
    for i, (dx, dy) in enumerate([(-14, -10), (12, -12), (-8, 12), (14, 10), (0, 0)]):
        col = (224, 38, 47) if i % 2 else (127, 230, 255)
        d.ellipse((c + dx - 17, c + dy - 17, c + dx + 17, c + dy + 17), fill=col + (255,), outline=(255, 255, 255, 180), width=2)
    ICON.parent.mkdir(exist_ok=True)
    img.save(ICON, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def shortcut(path):
    vbs = HERE / "Start Jarvis.vbs"
    wscript = r"C:\Windows\System32\wscript.exe"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');"
          f"$s.TargetPath='{wscript}';$s.Arguments='\"{vbs}\"';"
          f"$s.WorkingDirectory='{HERE}';$s.IconLocation='{ICON},0';$s.Description='Atomo voice assistant';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)


def folder(name):
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True).stdout.strip()
    return Path(out)


def main():
    make_icon()
    desktop = folder("Desktop")
    shortcut(desktop / "Atomo.lnk")
    shortcut(folder("Programs") / "Atomo.lnk")
    print(f"Atomo icon added to {desktop} and the Start menu")
    if "--exe" in sys.argv:
        build_exe(desktop)


def build_exe(desktop):
    if not CSC.exists():
        sys.exit("The .NET Framework C# compiler was not found (it ships with Windows 10/11).")
    pyw = shutil.which("pythonw") or ""
    if "WindowsApps" in pyw:                                # the Store alias, not a real Python
        pyw = str(Path(sys.executable).with_name("pythonw.exe"))
    src = HERE / "build" / "Atomo.cs"
    src.parent.mkdir(exist_ok=True)
    src.write_text(LAUNCHER.replace("__HOME__", str(HERE)).replace("__PYTHONW__", pyw), encoding="utf-8")
    subprocess.run([str(CSC), "/nologo", "/target:winexe", "/optimize+", f"/win32icon:{ICON}", f"/out:{EXE}", str(src)],
                   check=True)
    shutil.copy2(EXE, desktop / "Atomo.exe")
    print(f"built {EXE} and copied it to {desktop / 'Atomo.exe'}")


if __name__ == "__main__":
    main()
