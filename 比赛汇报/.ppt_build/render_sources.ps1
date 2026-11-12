$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$root=Split-Path $PSScriptRoot
$app=New-Object -ComObject PowerPoint.Application
$meta=@()
foreach($file in Get-ChildItem -LiteralPath $root -Filter '*.pptx') {
 $kind=if($file.Name.StartsWith('清华')){'template'}else{'source'}
 $dest=Join-Path $PSScriptRoot $kind
 New-Item -ItemType Directory -Path $dest -Force | Out-Null
 $deck=$app.Presentations.Open($file.FullName,$true,$false,$false)
 foreach($s in $deck.Slides) {
  $s.Export((Join-Path $dest ('slide-{0:00}.png' -f $s.SlideIndex)),'PNG',1600,900)
  $shapes=@()
  foreach($sh in $s.Shapes){
   $txt='';$font='';$sz=0;$rgb=0
   if($sh.HasTextFrame -and $sh.TextFrame.HasText){$txt=$sh.TextFrame.TextRange.Text;$font=$sh.TextFrame.TextRange.Font.Name;$sz=$sh.TextFrame.TextRange.Font.Size;$rgb=$sh.TextFrame.TextRange.Font.Color.RGB}
   $shapes+=@{name=$sh.Name;type=$sh.Type;text=$txt;font=$font;size=$sz;color=$rgb;x=$sh.Left;y=$sh.Top;w=$sh.Width;h=$sh.Height}
  }
  $meta+=@{kind=$kind;slide=$s.SlideIndex;layout=$s.CustomLayout.Name;shapes=$shapes}
 }
 $deck.Close()
}
$meta | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'shape_meta.json') -Encoding UTF8
Write-Output 'Source and template slides exported'
