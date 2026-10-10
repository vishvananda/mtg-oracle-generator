// JSONL adapter for the upstream mtgish parser; upstream sources stay unmodified.
package main

import (
    "bufio"
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"
    "strings"
    "regexp"
    "io"
    "strconv"
)

type OracleInput struct {
    Name string `json:"name"`
    TypeLine string `json:"type_line"`
    ManaCost string `json:"mana_cost"`
    OracleText string `json:"oracle_text"`
    Power string `json:"power"`
    Toughness string `json:"toughness"`
    Loyalty string `json:"loyalty"`
    Defense string `json:"defense"`
    Entry *MtgJsonEntry `json:"entry"`
}

var shortNames map[string]string
var ignored []string
var knownNames map[string]string
var knownMtgishTree TokenReTries
var knownQuotedNames TemplateAndResult

// Re-run the upstream debug path without changing its parser or grammar.
// Its farthest token is a search hint, not an exact root-cause location.
func failureDiagnostic(card CardInfo, english, mtgish TemplatesAndResults, englishTree, mtgishTree TokenReTries) map[string]any {
    stage, input, root := "english", card.EnglishText, "FullCard"
    grammar, tree := english, englishTree
    if card.MtgishErr != nil { stage="mtgish"; input=card.MtgishText;root="FULL_CARD";grammar=mtgish;tree=mtgishTree }
    if input=="" { return map[string]any{"stage":"preprocess"} }
    file, err := os.CreateTemp("", "mtgish-debug-*")
    if err!=nil { return map[string]any{"stage":stage} }
    defer os.Remove(file.Name()); defer file.Close()
    saved := os.Stdout; os.Stdout=file
    defer func(){os.Stdout=saved}()
    _, _ = TokenReTriesBestMatch(grammar,tree,root,input,true)
    os.Stdout=saved
    file.Seek(0,0)
    trace,_ := io.ReadAll(io.LimitReader(file,65536))
    match:=regexp.MustCompile(`Best token match: ([0-9]+)`).FindStringSubmatch(string(trace))
    if len(match)!=2 { return map[string]any{"stage":stage} }
    index,_:=strconv.Atoi(match[1]);tokens:=GetTokens(input)
    if index>len(tokens) { index=len(tokens) }
    prefix:=strings.Join(tokens[:index],"")
    remaining:=strings.Join(tokens[index:],"")
    before,after:=[]rune(prefix),[]rune(remaining)
    if len(before)>100 { before=before[len(before)-100:] };if len(after)>180 { after=after[:180] }
    line:=strings.Count(prefix,"\n")+1
    column:=len([]rune(prefix[strings.LastIndex(prefix,"\n")+1:]))+1
    token:="<end of input>";if index<len(tokens) { token=tokens[index] }
    return map[string]any{"stage":stage,"farthest_token_index":index,"line":line,"column":column,"token":token,
        "context":string(before)+"⟪HERE⟫"+string(after),
        "note":"Upstream debug farthest match, not a guaranteed error cause. Coordinates refer to normalized_input for english, generated mtgish for mtgish."}
}

func checkOracle(in OracleInput, english, mtgish TemplatesAndResults, englishTree TokenReTries) (result map[string]any) {
    saved := os.Stdout
    os.Stdout = os.Stderr // Upstream debug diagnostics must not corrupt JSONL replies.
    defer func() {
        os.Stdout = saved
        if failure := recover(); failure != nil {
            result = map[string]any{"status":"parser_error", "error":fmt.Sprint(failure), "parse_complete":false}
        }
    }()
    if in.Entry == nil && (len(in.OracleText) > 16000 || in.Name == "") {
        return map[string]any{"status":"invalid_input", "parse_complete":false}
    }
    if in.Entry == nil && strings.TrimSpace(in.OracleText) == "" {
        return map[string]any{"status":"empty", "parse_complete":false}
    }
    // Legacy flat inputs use the same loyalty formatting as preprocess_scryfall.
    loyalty := regexp.MustCompile(`(?m)^([+−-]?[0-9X]+):`)
    entry := MtgJsonEntry{Name:in.Name, Layout:"single", Cards:[]MtgJsonAtomic{{
        Name:in.Name, Type:in.TypeLine, ManaCost:in.ManaCost,
        Text:loyalty.ReplaceAllString(in.OracleText, "[$1]:"),
        Power:in.Power, Toughness:in.Toughness, Loyalty:in.Loyalty, Defense:in.Defense,
    }}}
    if in.Entry != nil { entry = *in.Entry }
    if Contains(ignored, entry.Name) {
        return map[string]any{"status":"ignored", "parse_complete":false, "error":"Excluded by upstream ignore.json5", "workflow":"upstream-standard-v2"}
    }
    mtgishTree := knownMtgishTree
    mtgish["QUOTED_NAME"] = knownQuotedNames
    missing := false
    for _,face := range entry.Cards { if _,ok:=knownNames[fmt.Sprintf("%#v",face.Name)]; !ok { missing=true } }
    if missing {
        names := make(map[string]string, len(knownNames)+len(entry.Cards))
        for k,v := range knownNames { names[k]=v }
        for _,face := range entry.Cards { q:=fmt.Sprintf("%#v",face.Name); names[q]=q }
        mtgish["QUOTED_NAME"] = LoadTemplatesFor(names)
        mtgishTree = CreateTokenReTriesFromTemplatesAndResults(mtgish)
    }
    findReplaceUsedInitialized = false
    card, err := ParseCard(english, englishTree, mtgish, mtgishTree, shortNames, entry)
    applied := []FindReplaceEntry{}
    for i,used := range findReplaceUsed { if used { applied=append(applied,findReplaceReasons[i]) } }
    status := "parsed"
    detail := ""
    if err != nil { status = "rejected"; detail = err.Error() }
    var diagnostic map[string]any
    if err != nil { diagnostic=failureDiagnostic(card,english,mtgish,englishTree,mtgishTree) }
    return map[string]any{"status":status, "parse_complete":err==nil,
        "error":detail, "normalized_input":card.EnglishText, "mtgish":card.MtgishText,
        "oracle_correctness":"not_proven", "intent_fidelity":"not_checked",
        "workflow":"upstream-standard-v2", "scope":"whole_card",
        "parse_diagnostic":diagnostic,
        "card_specific_rewrites":true, "applied_rewrites":applied,
        "representation_warning":func() string { if len(applied)>0 { return "Upstream text corrections applied; inspect normalized_input and applied_rewrites." }; return "" }()}
}

func main() {
    if len(os.Args)!=2 { panic("Supply the pinned grammar directory") }
    englishFiles, err := filepath.Glob(filepath.Join(os.Args[1],"english_grammar*.json5"))
    if err!=nil || len(englishFiles)==0 { panic("English grammar missing") }
    mtgishFiles, err := filepath.Glob(filepath.Join(os.Args[1],"mtgish_grammar*.json5"))
    if err!=nil || len(mtgishFiles)==0 { panic("mtgish grammar missing") }
    english := ReadGrammarJsoncFiles(englishFiles)
    mtgish := ReadGrammarJsoncFiles(mtgishFiles)
    englishTree := CreateTokenReTriesFromTemplatesAndResults(english)
    shortNames = ReadJsoncMap[string](filepath.Join(os.Args[1],"short_names.json5"))
    ignored = ReadJsoncArray[string](filepath.Join(os.Args[1],"ignore.json5"))
    findReplaceReasons = ReadJsoncArray[FindReplaceEntry](filepath.Join(os.Args[1],"find_replace.json5"))
    knownNames = map[string]string{}
    root := filepath.Dir(os.Args[1])
    for _,file := range []string{"oracle.json","dungeons.json","tokens.json","additional_cards.json"} {
        for _,entry := range ReadJsoncArray[MtgJsonEntry](filepath.Join(root,"data",file)) {
            if Contains(ignored,entry.Name) { continue }
            for _,face := range entry.Cards { if face.Type!="Stickers" { q:=fmt.Sprintf("%#v",face.Name);knownNames[q]=q } }
        }
    }
    scanner := bufio.NewScanner(os.Stdin)
    mtgish["QUOTED_NAME"] = LoadTemplatesFor(knownNames)
    knownQuotedNames = mtgish["QUOTED_NAME"]
    knownMtgishTree = CreateTokenReTriesFromTemplatesAndResults(mtgish)
    scanner.Buffer(make([]byte,4096),150000)
    encoder := json.NewEncoder(os.Stdout)
    for scanner.Scan() {
        var input OracleInput
        if err:=json.Unmarshal(scanner.Bytes(),&input); err!=nil {
            encoder.Encode(map[string]any{"status":"invalid_input", "error":err.Error(), "parse_complete":false})
            continue
        }
        encoder.Encode(checkOracle(input,english,mtgish,englishTree))
    }
    if err:=scanner.Err(); err!=nil { fmt.Fprintln(os.Stderr,err); os.Exit(1) }
}
