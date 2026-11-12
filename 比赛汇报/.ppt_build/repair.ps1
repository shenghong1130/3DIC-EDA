$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$root=Split-Path $PSScriptRoot
$app=New-Object -ComObject PowerPoint.Application
$d=$app.Presentations.Open((Join-Path $PSScriptRoot 'candidate.pptx'),$true,$false,$false)
$next=Join-Path $PSScriptRoot 'candidate_v2.pptx'
if(Test-Path $next){throw 'Revision exists'}
$d.SaveCopyAs($next,24);$d.Close()
$d=$app.Presentations.Open($next,$false,$false,$false)
$sources=Get-Content (Join-Path $PSScriptRoot 'sources.json') -Raw -Encoding UTF8|ConvertFrom-Json
$original=$sources.'3DIC比赛复盘_15页叙事版.pptx'.slides
$report=@()
foreach($s in $d.Slides){
 foreach($sh in $s.Shapes){
  if($sh.HasTextFrame -and $sh.TextFrame.HasText){
   $txt=$sh.TextFrame.TextRange.Text
   if($txt -match '^\d{2}$'){$sh.TextFrame.TextRange.ParagraphFormat.SpaceAfter=0;$sh.Top=514;$sh.Height=20}
   if($s.SlideIndex -eq 3 -and $txt.StartsWith('输入、算法、测试')){$sh.Top=438;$sh.TextFrame.TextRange.Font.Size=16;$sh.Height=45}
   if($s.SlideIndex -eq 7 -and $txt.StartsWith('单网在两个 Die')){$sh.Top=459;$sh.Height=23}
   if($s.SlideIndex -eq 15 -and $txt.StartsWith('公开结果无法替代')){$sh.TextFrame.TextRange.Text="公开结果无法替代三个隐藏案例的反馈`r截至 2026-11-11，最终成绩仍未知"}
  }
  if($s.SlideIndex -eq 7 -and $sh.Type -eq 13 -and $sh.Top -gt 100){$sh.LockAspectRatio=-1;$sh.Width=589;$sh.Left=55.5;$sh.Top=215}
 }
 $s.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text+="`r`r原始页面的补充技术信息：`r"+($original[$s.SlideIndex-1].text -join '')
 foreach($sh in $s.Shapes){
  if($sh.HasTextFrame -and $sh.TextFrame.HasText){
   $tf=$sh.TextFrame
   $report+=@{slide=$s.SlideIndex;name=$sh.Name;text=$tf.TextRange.Text;height=$sh.Height;boundHeight=$tf.TextRange.BoundHeight;x=$sh.Left;y=$sh.Top;w=$sh.Width;bottom=($sh.Top+$sh.Height)}
  }
 }
}
$d.Save()
$renders=Join-Path $PSScriptRoot 'final_render_v2';New-Item -ItemType Directory -Path $renders -Force|Out-Null
foreach($s in $d.Slides){$s.Export((Join-Path $renders ('slide-{0:00}.png' -f $s.SlideIndex)),'PNG',1600,900)}
$pdf=Join-Path $PSScriptRoot 'preview_v2.pdf';$d.SaveAs($pdf,32);$d.Close()
$report|ConvertTo-Json -Depth 5|Set-Content (Join-Path $PSScriptRoot 'text_fit.json') -Encoding UTF8
Write-Output 'Revision rendered and text bounds recorded'
