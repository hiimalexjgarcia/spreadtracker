Attribute VB_Name = "TrackerCore"
Option Explicit

' ---------------------------------------------------------------------------
' TrackerCore.bas
' Reads the generated workbook as a tracker song. These layout constants must
' match tools/build_workbook.py.
' ---------------------------------------------------------------------------

Public Const PATTERN_ROWS As Long = 64
Public Const CHANNEL_COUNT As Long = 4
Public Const BLOCK_ROWS As Long = 66
Public Const PATTERN_FIRST_DATA_ROW As Long = 6
Public Const ORDER_FIRST_ROW As Long = 23
Public Const INSTRUMENT_FIRST_ROW As Long = 4

Public Type TNote
    Period As Long        ' 0 = no note; otherwise an Amiga period value
    Sample As Long        ' 0 = no sample; otherwise 1..31
    Effect As Long        ' 0..15
    Param As Long         ' 0..255
End Type

Public Type TInstrument
    Name As String
    Bytes As Long
    Finetune As Long
    Volume As Long        ' 0..64
    RepeatStart As Long
    RepeatLength As Long  ' 0/1 = no loop; >1 = loop length in bytes
End Type

' ---------------------------------------------------------------------------
' Setting helpers (workbook defined names)
' ---------------------------------------------------------------------------

Public Function GetLongSetting(ByVal definedName As String) As Long
    GetLongSetting = CLng(ThisWorkbook.Names(definedName).RefersToRange.Value)
End Function

Public Function GetDoubleSetting(ByVal definedName As String) As Double
    GetDoubleSetting = CDbl(ThisWorkbook.Names(definedName).RefersToRange.Value)
End Function

Public Function GetTextSetting(ByVal definedName As String) As String
    GetTextSetting = CStr(ThisWorkbook.Names(definedName).RefersToRange.Value)
End Function

Public Function ResolvePath(ByVal pathText As String) As String
    Dim p As String
    p = Trim$(pathText)
    If Len(p) = 0 Then
        ResolvePath = vbNullString
        Exit Function
    End If
    If InStr(p, ":") > 0 Or Left$(p, 2) = "\\" Then
        ResolvePath = p
    Else
        ResolvePath = ThisWorkbook.Path & Application.PathSeparator & p
    End If
End Function

' ---------------------------------------------------------------------------
' Cell parsing
' ---------------------------------------------------------------------------

Private Function HexDigitValue(ByVal ch As String) As Long
    Dim c As String
    c = UCase$(Left$(ch, 1))
    If Len(c) = 0 Then Exit Function
    If c >= "0" And c <= "9" Then
        HexDigitValue = CLng(c)
    ElseIf c >= "A" And c <= "F" Then
        HexDigitValue = Asc(c) - Asc("A") + 10
    End If
End Function

Private Function NoteToPeriod(ByVal note As String) As Long
    Dim periods As Variant
    Dim names As Variant
    periods = Array( _
        856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453, _
        428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226, _
        214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 113)
    names = Array( _
        "C-1", "C#1", "D-1", "D#1", "E-1", "F-1", "F#1", "G-1", "G#1", "A-1", "A#1", "B-1", _
        "C-2", "C#2", "D-2", "D#2", "E-2", "F-2", "F#2", "G-2", "G#2", "A-2", "A#2", "B-2", _
        "C-3", "C#3", "D-3", "D#3", "E-3", "F-3", "F#3", "G-3", "G#3", "A-3", "A#3", "B-3")

    Dim i As Long
    For i = LBound(names) To UBound(names)
        If StrComp(Trim$(note), CStr(names(i)), vbTextCompare) = 0 Then
            NoteToPeriod = CLng(periods(i))
            Exit Function
        End If
    Next i
End Function

' Parses a cell such as "C-4 07 C40" or "--- -- ...".
Public Function ParseCell(ByVal text As String) As TNote
    Dim result As TNote
    Dim parts() As String
    Dim s As String
    s = Trim$(text)
    If Len(s) = 0 Then
        ParseCell = result
        Exit Function
    End If

    parts = Split(s, " ")
    If UBound(parts) >= 0 Then result.Period = NoteToPeriod(parts(0))

    If UBound(parts) >= 1 Then
        If IsNumeric(parts(1)) Then result.Sample = CLng(parts(1))
    End If

    If UBound(parts) >= 2 Then
        If Len(parts(2)) >= 3 And Left$(parts(2), 1) <> "." Then
            result.Effect = HexDigitValue(Mid$(parts(2), 1, 1))
            result.Param = HexDigitValue(Mid$(parts(2), 2, 1)) * 16 + _
                           HexDigitValue(Mid$(parts(2), 3, 1))
        End If
    End If

    ParseCell = result
End Function

' ---------------------------------------------------------------------------
' Worksheet access
' ---------------------------------------------------------------------------

' Fills notes(0..63, 0..3) from the Patterns sheet.
Public Sub ReadPattern(ByVal patternIndex As Long, ByRef notes() As TNote)
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("Patterns")

    Dim baseRow As Long
    baseRow = PATTERN_FIRST_DATA_ROW + patternIndex * BLOCK_ROWS

    ReDim notes(0 To PATTERN_ROWS - 1, 0 To CHANNEL_COUNT - 1)

    Dim r As Long, c As Long
    For r = 0 To PATTERN_ROWS - 1
        For c = 0 To CHANNEL_COUNT - 1
            notes(r, c) = ParseCell(CStr(ws.Cells(baseRow + r, 2 + c).Value))
        Next c
    Next r
End Sub

' Returns 0..N-1 pattern indices from the Song order list.
Public Function GetOrderList() As Long()
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("Song")

    Dim n As Long
    n = GetLongSetting("SongLength")

    Dim result() As Long
    If n <= 0 Then
        ReDim result(0 To 0)
        GetOrderList = result
        Exit Function
    End If

    ReDim result(0 To n - 1)
    Dim i As Long
    For i = 0 To n - 1
        result(i) = CLng(ws.Cells(ORDER_FIRST_ROW + i, 2).Value)
    Next i
    GetOrderList = result
End Function

Public Function GetInstrument(ByVal sampleNo As Long) As TInstrument
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("Instruments")

    Dim row As Long
    row = INSTRUMENT_FIRST_ROW + sampleNo - 1

    Dim result As TInstrument
    If sampleNo < 1 Or sampleNo > 31 Then
        GetInstrument = result
        Exit Function
    End If

    result.Name = CStr(ws.Cells(row, 2).Value)
    result.Bytes = CLng(ws.Cells(row, 3).Value)
    result.Finetune = CLng(ws.Cells(row, 4).Value)
    result.Volume = CLng(ws.Cells(row, 5).Value)
    result.RepeatStart = CLng(ws.Cells(row, 6).Value)
    result.RepeatLength = CLng(ws.Cells(row, 7).Value)

    GetInstrument = result
End Function

' Decodes the base64 chunks stored in the hidden SampleData sheet.
' Returns a Variant containing a Byte array, or vbNullString when absent.
Public Function GetSampleBytes(ByVal sampleNo As Long) As Variant
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("SampleData")

    Dim text As String
    Dim r As Long
    r = 2
    Do While Len(CStr(ws.Cells(r, 1).Value)) > 0
        If CLng(ws.Cells(r, 1).Value) = sampleNo Then
            text = text & CStr(ws.Cells(r, 3).Value)
        End If
        r = r + 1
    Loop

    If Len(text) = 0 Then
        GetSampleBytes = vbNullString
        Exit Function
    End If

    Dim doc As Object
    Dim node As Object
    Set doc = CreateObject("MSXML2.DOMDocument")
    Set node = doc.createElement("b64")
    node.dataType = "bin.base64"
    node.text = text
    GetSampleBytes = node.nodeTypedValue
End Function
