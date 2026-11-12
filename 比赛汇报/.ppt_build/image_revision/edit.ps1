$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$root=Split-Path (Split-Path $PSScriptRoot)
$app=New-Object -ComObject PowerPoint.Application
$input=Join-Path $root '3DIC比赛汇报_清华模板优化版.pptx'
$output=Join-Path $root '3DIC比赛汇报_清华模板优化版_v2.pptx'
$pdf=Join-Path $root '3DIC比赛汇报_清华模板优化版_v2.pdf'
if((Test-Path $output) -or (Test-Path $pdf)){throw 'Output already exists'}
$d=$app.Presentations.Open($input,$true,$false,$false)
$draft=Join-Path $PSScriptRoot 'edited.pptx'
$d.SaveCopyAs($draft,24);$d.Close();$d=$app.Presentations.Open($draft,$false,$false,$false)
$purple=8397420;$blue=12623968;$gray=14211288;$ink=2500134
function Txt($s,$text,$x,$y,$w,$h,$size=15,$color=$ink,$bold=0){
 $o=$s.Shapes.AddTextbox(1,$x,$y,$w,$h);$o.Name='Diagram_Label_'+$s.Shapes.Count
 $o.TextFrame.MarginLeft=0;$o.TextFrame.MarginRight=0;$o.TextFrame.MarginTop=0;$o.TextFrame.MarginBottom=0;$o.TextFrame.AutoSize=0
 $o.TextFrame.TextRange.Text=[string]$text;$o.TextFrame.TextRange.Font.Name='微软雅黑';$o.TextFrame.TextRange.Font.NameFarEast='微软雅黑';$o.TextFrame.TextRange.Font.Size=[single]$size;$o.TextFrame.TextRange.Font.Color.RGB=[int]$color
 if($bold -eq -1){$o.TextFrame.TextRange.Font.Bold=-1}else{$o.TextFrame.TextRange.Font.Bold=0}
 $o.TextFrame.WordWrap=-1;$o.Height=[Math]::Max($h,$o.TextFrame.TextRange.BoundHeight+1)
}
function Seg($s,$x1,$y1,$x2,$y2,$color=$purple,$weight=3){
 $o=$s.Shapes.AddLine($x1,$y1,$x2,$y2);$o.Name='Diagram_Path_'+$s.Shapes.Count;$o.Line.ForeColor.RGB=$color;$o.Line.Weight=[single]$weight
}
function Dot($s,$x,$y,$color=$purple){
 $o=$s.Shapes.AddShape(9,($x-4),($y-4),8,8);$o.Name='Diagram_PIN_'+$s.Shapes.Count;$o.Fill.Solid();$o.Fill.ForeColor.RGB=$color;$o.Line.Visible=0
}
function Grid($s,$x,$y,$cols,$rows,$step){
 for($i=0;$i -le $cols;$i++){Seg $s ($x+$i*$step) $y ($x+$i*$step) ($y+$rows*$step) $gray .65}
 for($i=0;$i -le $rows;$i++){Seg $s $x ($y+$i*$step) ($x+$cols*$step) ($y+$i*$step) $gray .65}
}
# Slide 7: two conceptual paths snapped to GCell centers.
$s=$d.Slides.Item(7)
$s.Shapes.Item('Picture 8').Delete()
$s.Shapes.Item('Text_7_11').TextFrame.TextRange.Text='GCell 中心连线与 L / Z 型候选路径（原理示意）'
Txt $s 'L 型候选' 62 216 245 27 17 $purple -1
Txt $s 'Z 型候选' 369 216 245 27 17 $blue -1
Grid $s 62 256 7 5 31
Grid $s 369 256 7 5 31
# Terminals and bends are at the centers of cells, rather than grid corners.
Seg $s 77.5 395.5 263.5 395.5
Seg $s 263.5 395.5 263.5 271.5
Dot $s 77.5 395.5;Dot $s 263.5 271.5
Seg $s 384.5 395.5 477.5 395.5 $blue
Seg $s 477.5 395.5 477.5 271.5 $blue
Seg $s 477.5 271.5 570.5 271.5 $blue
Dot $s 384.5 395.5 $blue;Dot $s 570.5 271.5 $blue
Txt $s 'PIN' 62 417 60 23 13
Txt $s '格中心拐弯' 185 417 120 23 13
Txt $s 'PIN' 369 417 60 23 13
Txt $s '整格通道覆盖' 477 417 150 23 13
$s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text+="`r`r图片修订说明：本页改用 PowerPoint 原生线条、网格和 PIN 形状绘制 GCell 中心连线的 L/Z 型路径原理示意。图形为概念示意，不是 v0.1–v0.4 的实际运行结果，不包含新增实验数据。"
# Slide 9: keep the actual supplied image, identify its conceptual use.
$s=$d.Slides.Item(9);$cap=$s.Shapes.Item('Text_9_6')
$cap.TextFrame.TextRange.Text="TSV 跨接 M10 与 M11`r结构示意，非该版本实际结果"
$cap.TextFrame.TextRange.Font.Size=13;$cap.Height=38
$s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text+="`r`r图片修订说明：保留用户提供的 TSV 跨层图片，仅作 M10/M11 结构说明。该截图不代表 v0.6–v0.8 的历史运行结果。"
# Slide 10: editable rectilinear shared-tree sketch, no experimental values.
$s=$d.Slides.Item(10);$s.Shapes.Item('Picture 6').Delete()
Txt $s '共享树布线原理' 610 183 298 30 18 $purple -1
Grid $s 622 237 9 6 29
# Common trunk: source S to branching point; two sink branches.
Seg $s 636.5 396.5 752.5 396.5 $purple 5
Seg $s 752.5 396.5 752.5 309.5 $purple 5
Seg $s 752.5 309.5 868.5 309.5 $blue 3
Seg $s 868.5 309.5 868.5 251.5 $blue 3
Seg $s 752.5 309.5 810.5 309.5 $blue 3
Seg $s 810.5 309.5 810.5 367.5 $blue 3
Dot $s 636.5 396.5;Dot $s 752.5 309.5
Dot $s 868.5 251.5 $blue;Dot $s 810.5 367.5 $blue
Txt $s 'S' 612 399 22 23 14 $purple -1
Txt $s 'T1' 875 242 33 23 14 $blue -1
Txt $s 'T2' 818 365 35 23 14 $blue -1
Txt $s '共享干线' 645 416 115 25 15 $purple -1
Txt $s '分支接入' 783 416 120 25 15 $blue -1
$cap=$s.Shapes.Item('Text_10_9');$cap.TextFrame.TextRange.Text='共享路径复用示意，非历史运行结果';$cap.TextFrame.TextRange.Font.Size=13;$cap.Height=23
$s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text+="`r`r图片修订说明：三维布线截图替换为 PowerPoint 原生形状绘制的共享树原理图。S 为源端，T1/T2 为接入端；紫色干线由分支共享。该图不代表 v0.9–v0.14 的实际布局、搜索结果或定量优化效果。"
$d.Save()
$render=Join-Path $PSScriptRoot 'render';New-Item -ItemType Directory -Path $render -Force|Out-Null
foreach($s in $d.Slides){$s.Export((Join-Path $render ('slide-{0:00}.png' -f $s.SlideIndex)),'PNG',1600,900)}
$d.SaveAs((Join-Path $PSScriptRoot 'edited.pdf'),32);$d.Close()
Write-Output 'Edited draft and all slide renders ready'
