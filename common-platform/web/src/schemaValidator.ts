import { Validator } from 'jsonschema';
import type { Schema, ValidationError } from 'jsonschema';
import type Ajv from 'ajv';
import type { ErrorObject, ValidateFunction } from 'ajv';

// JSON Forms consumes compile()/validate() and AJV-shaped errors. Its default
// AJV compiler uses new Function, which is intentionally forbidden by our CSP.
// jsonschema interprets the ordinary form keywords without generating code.
// Complete Draft 2020-12 validation remains authoritative on the server.
const validator=new Validator();
validator.customFormats.textarea=()=>true;
validator.customFormats['model-profile']=()=>true;
validator.customFormats['platform-file']=()=>true;
const pointer=(parts:(string|number)[])=>parts.length?'/'+parts.map(v=>String(v).replace(/~/g,'~0').replace(/\//g,'~1')).join('/'):'';
function message(error:ValidationError){const a=error.argument;switch(error.name){case'required':return '请填写此项';case'minLength':return `至少填写 ${a} 个字符`;case'maxLength':return `最多填写 ${a} 个字符`;case'pattern':return '请填写符合格式的内容';case'type':return '字段类型不符合要求';case'minimum':return `不能小于 ${a}`;case'maximum':return `不能大于 ${a}`;case'enum':return '请选择有效选项';default:return '请检查此项内容';}}
function compile(schema:Schema):ValidateFunction{
  const check=((data:unknown)=>{
    try{const result=validator.validate(data,schema,{allowUnknownAttributes:true,nestedErrors:true});check.errors=result.errors.map(error=>({instancePath:pointer(error.path),schemaPath:'#/'+error.name,keyword:error.name,params:error.name==='required'?{missingProperty:error.argument}:{},message:message(error)})) as ErrorObject[];return result.valid;}
    catch{check.errors=null;return true;}
  })as ValidateFunction;
  check.errors=null;return check;
}
export const schemaValidator={compile,validate(schema:Schema,data:unknown){const fn=compile(schema);return fn(data);},errors:null}as unknown as Ajv;
