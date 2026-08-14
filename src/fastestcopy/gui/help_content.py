"""Help content shown by HelpDialog, embedded as Python string constants
(not loaded from an external file) so it's guaranteed to be present in
the Nuitka onefile build without needing --include-data-files.
"""
from __future__ import annotations

HELP_JA = """
<h2>FastestCopy ヘルプ</h2>

<h3>基本的な使い方</h3>
<ol>
<li>上側の「コピー元」ペインでコピーしたいファイル/フォルダがあるドライブ・フォルダに移動します。</li>
<li>下側の「コピー先」ペインでコピー先のフォルダに移動します。</li>
<li>「コピー →」ボタンを押すとコピーが始まります。</li>
</ol>

<h3>ナビゲーションツリー(左側)</h3>
<ul>
<li><b>PC</b>: ローカルドライブ、マップ済みネットワークドライブ、「ネットワークの場所」ショートカットの一覧。</li>
<li><b>ネットワーク</b>: ネットワーク上のコンピューター(BEELINK など)を表示。展開すると各コンピューターの共有フォルダが表示されます。</li>
<li>アドレスバーに直接パス(UNCパス <code>\\\\server\\share</code> も可)を入力して移動できます。</li>
</ul>

<h3>ファイルの選択</h3>
<ul>
<li>右側のファイル一覧で、通常クリックは単一選択、<b>Shift+クリック</b>で範囲選択、<b>Ctrl+クリック</b>で個別選択の追加/解除ができます。</li>
<li>フォルダを選択してコピーすると、コピー先に同名のサブフォルダが作られます(Explorerと同じ動作)。</li>
<li>何も選択せずにコピーすると、現在開いているフォルダ自体がコピー対象になります。</li>
</ul>

<h3>隠しファイル/フォルダを表示</h3>
<p>デフォルトでは非表示ですが、チェックを入れるとWindowsの「隠し」属性を持つファイル/フォルダも表示されます。</p>

<h3>競合ポリシー</h3>
<ul>
<li><b>スキップ (既存を保持)</b>: コピー先に同名ファイルがあれば何もしません。</li>
<li><b>上書き</b>: 常に上書きします。</li>
<li><b>新しい方のみ上書き</b>: サイズが違う、またはコピー元の更新日時がより新しい場合のみ上書きします。</li>
<li><b>毎回確認</b>: 競合するたびにダイアログで確認します。</li>
</ul>

<h3>スキャンしてコピー</h3>
<p>「スキャンしてコピー →」ボタンを使うと、実際にコピーを始める前に対象全体を読み取り専用でスキャンし、
「コピー対象は何件、スキップされるのは何件」という内訳を確認できます。内容を確認してから実行するかどうかを選べます。
大量のファイルを扱う際、実際に何が起きるかを事前に把握したい場合に便利です。</p>

<h3>コピー中のキャンセル</h3>
<p>コピー中はいつでもキャンセルできます。大容量ファイルのコピー中でも、途中で素早く中断されます
(書きかけの不完全なファイルは自動的に削除されます)。</p>

<h3>エラーログ</h3>
<p>コピー中にエラーが発生した場合、ファイル名と原因を記録したログファイルが
<code>%LOCALAPPDATA%\\FastestCopy\\logs\\</code> に自動保存されます。
完了ダイアログの「エラーログを開く」ボタンから直接開けます。</p>

<h3>管理者権限</h3>
<p>管理者として実行すると、巨大ファイルのコピー時にNTFSのゼロ埋め処理を省略できるため、
特定条件下でさらに高速になります。「ツール」メニューから管理者として再起動できます。</p>
"""

HELP_EN = """
<h2>FastestCopy Help</h2>

<h3>Basic Usage</h3>
<ol>
<li>In the top "Source" pane, navigate to the drive/folder containing what you want to copy.</li>
<li>In the bottom "Target" pane, navigate to the destination folder.</li>
<li>Click "Copy →" to start copying.</li>
</ol>

<h3>Navigation Tree (left side)</h3>
<ul>
<li><b>PC</b>: local drives, mapped network drives, and "network location" shortcuts.</li>
<li><b>Network</b>: computers visible on the network (e.g. BEELINK). Expand one to browse its shared folders.</li>
<li>You can also type a path directly into the address bar (UNC paths like <code>\\\\server\\share</code> work too).</li>
</ul>

<h3>Selecting Files</h3>
<ul>
<li>In the file list on the right: a plain click selects one item, <b>Shift+click</b> selects a range,
and <b>Ctrl+click</b> toggles individual items in/out of the selection.</li>
<li>Copying a selected folder creates a same-named subfolder at the destination (same as Explorer).</li>
<li>With nothing selected, the currently open folder itself becomes the copy source.</li>
</ul>

<h3>Show Hidden Files/Folders</h3>
<p>Hidden by default; check this to also show files/folders with the Windows "Hidden" attribute.</p>

<h3>Conflict Policy</h3>
<ul>
<li><b>Skip (keep existing)</b>: does nothing if a same-named file already exists at the destination.</li>
<li><b>Overwrite</b>: always replaces the destination file.</li>
<li><b>Overwrite if newer</b>: replaces only if the size differs or the source is newer.</li>
<li><b>Ask every time</b>: prompts with a dialog on every conflict.</li>
</ul>

<h3>Scan then Copy</h3>
<p>The "Scan then Copy →" button does a read-only scan of everything before actually copying anything,
showing exactly how many files will be copied vs. skipped. You can review the plan and decide whether
to proceed. Useful when working with a large number of files and you want to know what will actually
happen before it happens.</p>

<h3>Cancelling Mid-Copy</h3>
<p>You can cancel at any time. Even mid-copy of a very large file, cancelling takes effect almost
immediately (any partially-written destination file is cleaned up automatically).</p>

<h3>Error Log</h3>
<p>If any files fail to copy, a log listing each filename and the reason is automatically saved to
<code>%LOCALAPPDATA%\\FastestCopy\\logs\\</code>. The completion dialog's "Open Error Log" button opens
it directly.</p>

<h3>Administrator Privilege</h3>
<p>Running as administrator lets large-file copies skip NTFS's zero-fill step, making them faster under
certain conditions. You can restart as administrator from the "Tools" menu.</p>
"""
