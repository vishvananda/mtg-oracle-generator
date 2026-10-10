package main

import (
    "embed"
    "encoding/json"
    "fmt"
    "io/fs"
    "runtime/debug"
    "syscall/js"
)

//go:embed grammars/*.json5 names.json
var browserFiles embed.FS
var browserFarthest int
var browserCheck js.Func

func main() {
    // WASM linear memory cannot shrink. Collect temporary grammar allocations
    // early so startup does not leave a large permanent high-water allocation.
    debug.SetGCPercent(25)
    englishFiles, _ := fs.Glob(browserFiles,"grammars/english_grammar*.json5")
    mtgishFiles, _ := fs.Glob(browserFiles,"grammars/mtgish_grammar*.json5")
    english := ReadGrammarJsoncFiles(englishFiles)
    mtgish := ReadGrammarJsoncFiles(mtgishFiles)
    englishTree := CreateTokenReTriesFromTemplatesAndResults(english)
    shortNames = ReadJsoncMap[string]("grammars/short_names.json5")
    ignored = ReadJsoncArray[string]("grammars/ignore.json5")
    findReplaceReasons = ReadJsoncArray[FindReplaceEntry]("grammars/find_replace.json5")
    knownNames = map[string]string{}
    for _,name := range ReadJsoncArray[string]("names.json") {
        if !Contains(ignored,name) { q:=fmt.Sprintf("%#v",name);knownNames[q]=q }
    }
    mtgish["QUOTED_NAME"] = LoadTemplatesFor(knownNames)
    knownQuotedNames = mtgish["QUOTED_NAME"]
    knownMtgishTree = CreateTokenReTriesFromTemplatesAndResults(mtgish)
    browserCheck = js.FuncOf(func(this js.Value,args []js.Value) any {
        var input OracleInput
        if len(args)!=1 || json.Unmarshal([]byte(args[0].String()),&input)!=nil {
            return `{"status":"invalid_input","parse_complete":false}`
        }
        result:=checkOracle(input,english,mtgish,englishTree)
        encoded,err:=json.Marshal(result)
        if err!=nil { return `{"status":"parser_error","parse_complete":false}` }
        return string(encoded)
    })
    js.Global().Set("mtgishCheck",browserCheck)
    js.Global().Call("mtgishReady")
    select {}
}
