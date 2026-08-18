QuickFileCopy - Native high-speed file copy for Windows
Distribution package  v0.1.0 development preview

GitHub
------
https://github.com/maktak-105/QuickFileCopy

Requirements
------------
- Windows 10 / 11 (64-bit)
- Microsoft Edge WebView2 Runtime
- Administrator approval through UAC for complete-preservation modes

Installation
------------
Extract every file from QuickFileCopy-binary.zip into the same folder.
Run QuickFileCopy.exe. The GUI is embedded in the executable, so index.html is not required.
WebView2Loader.dll must remain beside QuickFileCopy.exe.

GUI usage
---------
1. Select one or more source folders.
2. Select a destination folder.
3. Select whether to merge source contents or create each source folder below the destination.
4. Select skip, overwrite, or overwrite-if-newer and choose a copy mode.
5. Start copying and monitor byte and file progress.

Use the QDB-style language button at the upper right to switch between English and Japanese.
The selected language is restored the next time the application starts.

CLI usage
---------
QuickFileCopy_cli.exe copy C:\Source D:\Backup --contents --policy newer
QuickFileCopy_cli.exe copy C:\Source D:\Backup --folder --policy overwrite --verify
QuickFileCopy_cli.exe --help

Distribution files
------------------
- QuickFileCopy.exe - WebView2 GUI with embedded HTML
- QuickFileCopy_cli.exe - command-line version
- WebView2Loader.dll - WebView2 loader, not the Runtime itself
- readme.txt / readme_jp.txt - user documentation
- history.txt / history_jp.txt - change log
- LICENSE.txt / LICENSE_jp.txt - MIT License files

SHA-256
-------
DD5CB93CF84CD6FE5D0DF78DC1A640B3CF97EF4E153989CC8F39A7EF681651E7  QuickFileCopy.exe
5CAE1046B51B5D60EB929561294E3E2F526C62CAA97A828BA9B326F693E08843  QuickFileCopy_cli.exe

License
-------
This software is provided under the MIT License. See LICENSE.txt. LICENSE_jp.txt is a Japanese
reference translation; the English original controls if the texts differ.

Disclaimer
----------
This software is provided as-is. The author assumes no responsibility for its use, results,
data loss, system failure, hardware damage, or other damages. Back up important data and test
with non-critical files first.

QuickFileCopy is independent and is not affiliated with or endorsed by Microsoft, FastCopy,
Robocopy, Box, or any other third-party vendor.
