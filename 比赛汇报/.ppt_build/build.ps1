$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$root=Split-Path $PSScriptRoot
$plan=Get-Content (Join-Path $PSScriptRoot 'plan.json') -Raw -Encoding UTF8|ConvertFrom-Json
$images=Get-Content (Join-Path $PSScriptRoot 'images_meta.json') -Raw -Encoding UTF8|ConvertFrom-Json
$colors=@{purple=8397420;ink=2500134;muted=6710886;white=16777215;blue=12623968;gold=3514049}
$app=New-Object -ComObject PowerPoint.Application
$srcFile=Get-ChildItem -LiteralPath $root -Filter '3DIC*.pptx'|Where-Object {$_.Name -like '*15*'}|Select-Object -First 1
$template=Get-ChildItem -LiteralPath $root -Filter '*.pptx'|Where-Object {$_.Length -gt 30000000}|Select-Object -First 1
$src=$app.Presentations.Open($srcFile.FullName,$true,$false,$false)
$tmpl=$app.Presentations.Open($template.FullName,$true,$false,$false)
$draft=Join-Path $PSScriptRoot 'candidate.pptx'
if(Test-Path -LiteralPath $draft){throw 'Candidate already exists; select a new revision filename'}
$tmpl.SaveCopyAs($draft,24)
$tmpl.Close()
$deck=$app.Presentations.Open($draft,$false,$false,$false)
for($i=$deck.Slides.Count;$i -ge 1;$i--){if($i -ne 1 -and $i -ne 11){$deck.Slides.Item($i).Delete()}}
$base=$deck.Slides.Item(2)
for($i=$base.Shapes.Count;$i -ge 1;$i--){
 $sh=$base.Shapes.Item($i)
 if($sh.Name -notin @('小李PPT1','直线连接符 3','图片 24')){$sh.Delete()}
}
while($deck.Slides.Count -lt 15){$r=$deck.Slides.Item(2).Duplicate();$r.Item(1).MoveTo($deck.Slides.Count)}
function AddText($s,$e){
 $sh=$s.Shapes.AddTextbox(1,[single]$e.x,[single]$e.y,[single]$e.w,[single]$e.h)
 $sh.Name='Text_'+$s.SlideIndex+'_'+$s.Shapes.Count
 $tf=$sh.TextFrame;$tf.MarginLeft=0;$tf.MarginRight=0;$tf.MarginTop=0;$tf.MarginBottom=0;$tf.WordWrap=-1;$tf.AutoSize=0
 $tf.TextRange.Text=$e.text.Replace("`n","`r")
 $f=$tf.TextRange.Font;$f.Name='微软雅黑';$f.NameFarEast='微软雅黑';$f.Size=[single]$e.size;$f.Bold=if($e.bold){-1}else{0};$f.Color.RGB=$colors[$e.color]
 $tf.TextRange.ParagraphFormat.SpaceAfter=5
 return $sh
}
function AddPic($s,$e){
 $im=$images[$e.id-1];$ratio=[double]$im.size[0]/$im.size[1]
 $w=[double]$e.w;$h=$w/$ratio
 if($h -gt [double]$e.h){$h=[double]$e.h;$w=$h*$ratio}
 $x=[double]$e.x+([double]$e.w-$w)/2;$y=[double]$e.y+([double]$e.h-$h)/2
 $p=$s.Shapes.AddPicture((Join-Path (Join-Path $root 'images') $im.file),0,-1,[single]$x,[single]$y,[single]$w,[single]$h)
 $p.AlternativeText=$e.caption+'；'+$im.file+'；完整保留，等比缩放'
 $null=AddText $s @{text=$e.caption;x=$e.x;y=([double]$e.y+[double]$e.h+4);w=$e.w;h=27;size=14;bold=$false;color='muted'}
}
function AddTable($s,$e){
 $nr=$e.rows.Count;$nc=$e.rows[0].Count
 $shape=$s.Shapes.AddTable($nr,$nc,[single]$e.x,[single]$e.y,[single]$e.w,[single]$e.h)
 $t=$shape.Table
 for($c=1;$c -le $nc;$c++){$t.Columns.Item($c).Width=[single]([double]$e.w*[double]$e.widths[$c-1])}
 for($r=1;$r -le $nr;$r++){
  $t.Rows.Item($r).Height=[single]([double]$e.h/$nr)
  for($c=1;$c -le $nc;$c++){
   $sh=$t.Cell($r,$c).Shape;$sh.TextFrame.TextRange.Text=[string]$e.rows[$r-1][$c-1]
   $sh.TextFrame.MarginLeft=10;$sh.TextFrame.MarginRight=8;$sh.TextFrame.MarginTop=4;$sh.TextFrame.MarginBottom=4;$sh.TextFrame.VerticalAnchor=3
   $f=$sh.TextFrame.TextRange.Font;$f.Name='微软雅黑';$f.NameFarEast='微软雅黑';$f.Size=[single]$e.size
   $sh.Fill.Solid()
   if($r -eq 1){$sh.Fill.ForeColor.RGB=$colors.purple;$f.Color.RGB=$colors.white;$f.Bold=-1}else{$sh.Fill.ForeColor.RGB=if($r%2 -eq 0){16119285}else{16777215};$f.Color.RGB=$colors.ink;$f.Bold=0}
   foreach($edge in 1..4){$t.Cell($r,$c).Borders.Item($edge).ForeColor.RGB=15132390;$t.Cell($r,$c).Borders.Item($edge).Weight=.5}
  }
 }
}
function AddChart($s,$e){
 $found=$null
 foreach($sh in $src.Slides.Item([int]$e.source).Shapes){if($sh.HasChart){$found=$sh;break}}
 if($null -eq $found){throw "Missing chart $($e.source)"}
 $found.Copy();$r=$s.Shapes.Paste();$p=$r.Item(1)
 $p.Left=[single]$e.x;$p.Top=[single]$e.y;$p.Width=[single]$e.w;$p.Height=[single]$e.h
 $ch=$p.Chart
 $ch.ChartArea.Format.Fill.Solid();$ch.ChartArea.Format.Fill.ForeColor.RGB=16777215;$ch.ChartArea.Format.Line.Visible=0
 $ch.ChartArea.Font.Name='微软雅黑';$ch.ChartArea.Font.Size=14
 $palette=@($colors.purple,12623968,3514049)
 for($j=1;$j -le $ch.SeriesCollection().Count;$j++){
  $ser=$ch.SeriesCollection($j);$ser.Format.Fill.Solid();$ser.Format.Fill.ForeColor.RGB=$palette[($j-1)%3];$ser.Format.Line.Visible=0
  $ser.DataLabels().Font.Name='微软雅黑';$ser.DataLabels().Font.Size=14;$ser.DataLabels().NumberFormat='0.00'
  if($e.source -eq 13){$ser.DataLabels().NumberFormat='0'}
 }
 foreach($k in 1..2){$ch.Axes($k).TickLabels.Font.Name='微软雅黑';$ch.Axes($k).TickLabels.Font.Size=13}
 if($ch.HasLegend){$ch.Legend.Font.Name='微软雅黑';$ch.Legend.Font.Size=14}
}
for($n=1;$n -le 15;$n++){
 $s=$deck.Slides.Item($n);$d=$plan[$n-1]
 if($n -eq 1){
  foreach($sh in $s.Shapes){if($sh.HasTextFrame -and $sh.TextFrame.HasText){
   $t=$sh.TextFrame.TextRange.Text
   if($t -like '*添加标题描述*'){$sh.TextFrame.TextRange.Text='2026 EDA 精英挑战赛 · 赛题八';$sh.Width=650}
   elseif($t -like '*演示文稿模板*'){$sh.TextFrame.TextRange.Text="3DIC 布线优化`r与 Agent 能力复盘";$sh.Left=76;$sh.Top=314;$sh.Width=680;$sh.Height=89;$sh.TextFrame.TextRange.Font.Size=34;$sh.TextFrame.MarginTop=0;$sh.TextFrame.MarginBottom=0}
   elseif($t -like '*汇报人*'){$sh.TextFrame.TextRange.Text='比赛技术复盘      2026 年 11 月';$sh.Top=422;$sh.Width=650}
  }}
  foreach($sh in $s.Shapes){if($sh.Type -eq 9){$sh.Top=409;$sh.Width=640}}
 }else{
  foreach($sh in $s.Shapes){if($sh.Name -eq '小李PPT1'){$sh.TextFrame.TextRange.Text=$d.title;$sh.Width=755;$sh.TextFrame.TextRange.Font.Size=27;$sh.Height=42}}
  foreach($e in $d.elements){switch($e.type){'text'{$null=AddText $s $e};'image'{AddPic $s $e};'table'{AddTable $s $e};'chart'{AddChart $s $e}}}
  $null=AddText $s @{text=('{0:00}' -f $n);x=884;y=519;w=32;h=16;size=10;bold=$false;color='muted'}
 }
 $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text=$d.notes
 Write-Output "Built slide $n"
}
$deck.Save()
$render=Join-Path $PSScriptRoot 'final_render';New-Item -ItemType Directory -Path $render -Force|Out-Null
foreach($s in $deck.Slides){$s.Export((Join-Path $render ('slide-{0:00}.png' -f $s.SlideIndex)),'PNG',1600,900)}
$deck.SaveAs((Join-Path $PSScriptRoot 'preview.pdf'),32)
$deck.Close();$src.Close()
Write-Output 'Draft and previews ready'
