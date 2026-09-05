# GitHub で Windows 版を配布する方法

このリポジトリには、Windows 用の配布 ZIP を GitHub Releases に自動添付するワークフローがあります。ZIP には `NoesiaHR.exe` と、起動に必要な `_internal` フォルダをまとめて含めます。

## 初回公開

1. このファイルと `.github/workflows/release.yml` を GitHub に push します。
2. GitHub のリポジトリ画面で **Actions** → **Windows EXE release** → **Run workflow** を開きます。
3. `release_tag` に `v1.0.0` のようなバージョンを入力して実行します。
4. 完了後、リポジトリの **Releases** に `NoesiaHR-v1.0.0-windows.zip` が表示されます。利用者には、その ZIP をダウンロードして展開してもらいます。

以降は、`v1.0.1` のように `v` で始まる Git タグを push しても、同じ処理が自動実行されます。

```powershell
git tag v1.0.1
git push origin v1.0.1
```

## 利用者への案内

- ZIP を解凍し、フォルダ内の `NoesiaHR.exe` を起動してください。`_internal` フォルダは削除・移動しないでください。
- Windows の警告で ZIP がブロックされた場合は、解凍前に ZIP を右クリック → **プロパティ** → **許可する** を選んでください。
- 初回起動後、画面右上の設定から Noesia API キーを入力します。API キーは利用者の PC 内にだけ保存されます。
