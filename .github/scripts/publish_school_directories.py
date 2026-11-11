"""Publish verified course bytes as browsable school/course/source directories.

Runs locally with --plan-only, or on the repository's manual GitHub runner.
Course archives are data: their contents are never executed.
"""
from __future__ import annotations
import argparse,base64,collections,hashlib,html,json,os,pathlib,re,subprocess,tempfile,time,unicodedata,urllib.error,urllib.parse,urllib.request,zipfile

REPO='zer47775-sketch/985-course-materials'
REPO_ID=1454032300
RELEASE_ID=441750661
COURSES=('高等数学','线性代数','大学物理')
EXPECTED={
 '高等数学':('01-calculus-985.zip',2377,1688996057,'4c1ae5f42214810c24a5efd6b465930d4f5dc8270d5c2e0a54bfc1e96d780dd7'),
 '线性代数':('02-linear-algebra-985.zip',902,800797070,'6b3fc2031995c58439c3dc2d4e0bb9962d5fcfa0533c301762edcff83bd41e58'),
 '大学物理':('03-university-physics-985.zip',858,1050989168,'1e34d584f3c1f94d5bb4114d15ad079179bb0e9e8a68adc4ba9e1e17a1963fb7')}
TOTAL_BYTES=3515991542
BATCH_BYTES=256*1024**2
BEGIN='<!-- SCHOOL_DIRECTORY_BEGIN -->'
END='<!-- SCHOOL_DIRECTORY_END -->'

def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as stream:
  for data in iter(lambda:stream.read(4*1024**2),b''):h.update(data)
 return h.hexdigest()

def component(text,limit=100):
 text=unicodedata.normalize('NFC',str(text))
 clean=re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',text).strip().rstrip('. ') or '未命名'
 if clean in {'.','..'}:clean='_'
 if clean.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:clean='_'+clean
 if len(clean)>limit:
  ext=pathlib.PurePosixPath(clean).suffix
  if len(ext)>12:ext=''
  clean=clean[:limit-len(ext)-12]+'__'+hashlib.sha256(text.encode()).hexdigest()[:10]+ext
 if len(clean.encode('utf-8'))>240:
  ext=pathlib.PurePosixPath(clean).suffix
  suffix='__'+hashlib.sha256(text.encode()).hexdigest()[:10]+ext
  stem=clean[:-len(ext)] if ext else clean
  while len((stem+suffix).encode('utf-8'))>240:stem=stem[:-1]
  clean=stem+suffix
 return clean

def label(text):
 s=html.escape(str(text).replace('\n',' '),quote=False)
 for ch in ('\\','|','[',']','*','_','`'):s=s.replace(ch,'\\'+ch)
 return s

def link(path):return urllib.parse.quote(str(path).replace('\\','/'),safe='/')
def school_folder(s):return f"{s['priority_order']+1:03d}_"+component(s['school'],70)
def school_wrapper(part,school):
 return re.fullmatch(r'(?:【第\d+期】)?'+re.escape(unicodedata.normalize('NFKC',school))+r'(?:\(\d+\))?',unicodedata.normalize('NFKC',part)) is not None

def build_plan(manifest,catalog):
 schools=sorted((s for s in catalog['schools'] if s['tier']<=2),key=lambda s:s['priority_order'])
 by_school={s['school']:s for s in schools}
 files=[dict(f) for f in manifest['files'] if f['status']=='complete']
 if len(files)!=4137 or sum(f['size'] for f in files)!=TOTAL_BYTES:raise RuntimeError('Material manifest count/bytes differs')
 used={};seen_members=set()
 for f in sorted(files,key=lambda x:(x['school_order'],COURSES.index(x['course']),x['original_path'],x['id'])):
  if f['course'] not in COURSES or f['tier']>2 or f['school'] not in by_school:raise RuntimeError('Out-of-scope material')
  if f['size']>=100*1024**2 or not re.fullmatch('[a-f0-9]{64}',f['sha256']):raise RuntimeError('Invalid file identity or size')
  local=f['local_path'].replace('\\','/')
  if local.startswith('/') or '..' in pathlib.PurePosixPath(local).parts:raise RuntimeError('Unsafe source member')
  member='高校课程资料/'+local
  if member in seen_members:raise RuntimeError('Repeated source member')
  seen_members.add(member)
  original=f['original_path'].split('/')
  while len(original)>1 and (school_wrapper(original[0],f['school']) or original[0]=='（分享内搜索）'):original.pop(0)
  if len(original)>1 and original[0]==f['course']:original.pop(0)
  parts=[component(p,90 if i<len(original)-1 else 110) for i,p in enumerate(original)]
  if not parts:raise RuntimeError('Empty source path')
  folder=school_folder(by_school[f['school']])
  relative='/'.join(parts)
  target='大学资料/'+folder+'/'+f['course']+'/'+relative
  if target.casefold() in used or (len(parts)==1 and parts[0].casefold()=='readme.md'):
   p=pathlib.PurePosixPath(parts[-1]);parts[-1]=p.stem+'__'+f['id'].split(':')[-1][:8]+p.suffix
   relative='/'.join(parts);target='大学资料/'+folder+'/'+f['course']+'/'+relative
  if target.casefold() in used:raise RuntimeError('Target path collision')
  used[target.casefold()]=f['id']
  f.update(repo_path=target,school_folder=folder,course_relative=relative,source_member=member)
 for course,(_,count,_,_) in EXPECTED.items():
  if sum(f['course']==course for f in files)!=count:raise RuntimeError('Course file count differs')
 return schools,files

def write_docs(root,schools,files,manifest):
 grouped=collections.defaultdict(list)
 for f in files:grouped[(f['school'],f['course'])].append(f)
 failures=collections.defaultdict(list)
 for f in manifest['files']:
  if f['status']=='error':failures[(f['school'],f['course'])].append(f)
 shares={s['school']:s for s in manifest['shares']}
 table=['| 学校 | 高等数学 | 线性代数 | 大学物理 | 已收录 |','|---|---:|---:|---:|---:|']
 for s in schools:
  folder=school_folder(s);base='大学资料/'+folder
  counts=[len(grouped[(s['school'],c)]) for c in COURSES]
  cells=[f"[{label(s['school'])}]({link(base+'/README.md')})"]
  cells += [f"[{n}]({link(base+'/'+c+'/README.md')})" for c,n in zip(COURSES,counts)]
  cells += [str(sum(counts))];table.append('| '+' | '.join(cells)+' |')
  home=root/base;home.mkdir(parents=True,exist_ok=True)
  reason=''
  share=shares.get(s['school'])
  if not sum(counts):
   reason='原站没有提供该校区的单独资源链接。' if not s.get('resources') else ('原分享未能读取：'+str(share.get('error','')) if share else '原站暂无可用文件。')
  body=[f"# {s['school']}",'',f"[全部大学](../README.md) · [按学科浏览](../../学科目录/README.md)",'',
        f"分组：{s['tier_label']}。已收录{sum(counts)}份原文件。",'', '| 学科 | 文件数 | 目录 |','|---|---:|---|']
  for c,n in zip(COURSES,counts):body.append(f"| {c} | {n} | [打开目录]({link(c+'/README.md')}) |")
  if reason:body+=['',reason]
  source=share.get('url') if share else next(iter(s.get('resources',{}).values()),None)
  if source:body+=['',f'[原始学校分享]({source})']
  (home/'README.md').write_text('\n'.join(body)+'\n',encoding='utf-8')
  for c in COURSES:
   course_dir=home/c;course_dir.mkdir(parents=True,exist_ok=True)
   entries=sorted(grouped[(s['school'],c)],key=lambda f:f['course_relative'])
   lines=[f"# {s['school']} · {c}",'', '[学校首页](../README.md) · [全部大学](../../README.md)', '',f'已收录{len(entries)}份文件。点击文件名预览，点击“下载”获取单份文件。','']
   folders=collections.defaultdict(list)
   for f in entries:folders[str(pathlib.PurePosixPath(f['course_relative']).parent)].append(f)
   for sub,items in folders.items():
    if sub!='.':lines+=['### '+label(sub),'']
    lines+=['| 文件 | 大小 | 下载 |','|---|---:|---|']
    for f in items:
     rel=link(f['course_relative']);size=f"{f['size']/1048576:.2f} MB" if f['size']>=1048576 else f"{f['size']/1024:.1f} KB"
     lines.append(f"| [{label(pathlib.PurePosixPath(f['course_relative']).name)}]({rel}) | {size} | [下载]({rel}?raw=true) |")
    lines+=['']
   if not entries:lines+=['暂无已上传文件。'+reason,'']
   failed=failures[(s['school'],c)]
   if failed:
    lines+=['## 原站未能下载的文件','']
    for f in failed:lines.append('- '+label(f['filename'])+'：原分享访客下载限制，未计入已收录数量。')
   (course_dir/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 root_index=['# 按大学浏览','', '[仓库首页](../README.md) · [按学科浏览](../学科目录/README.md)','',
             f'共有{len(schools)}条学校及校区记录，{len({f["school"] for f in files})}个可用学校目录，4137份原文件。',
             '按清北、其他C9、其他985分组；同组编号用于排序，不代表校际排名。','']
 # Links in this index are relative to the 大学资料 directory.
 university_table=[row.replace('](%E5%A4%A7%E5%AD%A6%E8%B5%84%E6%96%99/','](') for row in table]
 (root/'大学资料'/'README.md').write_text('\n'.join(root_index+university_table)+'\n',encoding='utf-8')
 subjects=root/'学科目录';subjects.mkdir(exist_ok=True)
 (subjects/'README.md').write_text('# 按学科浏览\n\n[全部大学](../大学资料/README.md)\n\n'+''.join(f'- [{c}]({link(c+"/README.md")})\n' for c in COURSES),encoding='utf-8')
 for c in COURSES:
  path=subjects/c;path.mkdir(exist_ok=True)
  lines=[f'# {c}','', '[全部学科](../README.md) · [全部大学](../../大学资料/README.md)','', '| 学校 | 已收录文件 | 目录 |','|---|---:|---|']
  for s in schools:
   n=len(grouped[(s['school'],c)]);target='../../大学资料/'+school_folder(s)+'/'+c+'/README.md'
   lines.append(f"| {label(s['school'])} | {n} | [打开文件目录]({link(target)}) |")
  (path/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
 homepage=root/'README.md';old=homepage.read_text('utf-8') if homepage.exists() else '# 985高校三门课程资料\n'
 section='\n'.join([BEGIN,'## 逐校逐科浏览原文件','',
   '**[按大学浏览](大学资料/README.md) · [按学科浏览](学科目录/README.md)**','',
   '学校和课程目录中可以直接打开每份PDF、课件和图片，也可以单独下载。原始考试、年份及课件分组保留在子目录中。','',*table,'',END])
 if BEGIN in old:
  a=old.index(BEGIN);b=old.index(END,a)+len(END);new=old[:a]+section+old[b:]
 else:
  first,sep,rest=old.partition('\n');new=first+'\n\n'+section+'\n\n'+rest.lstrip('\n')
 homepage.write_text(new,encoding='utf-8')
 listing=[{k:f[k] for k in ('id','school','course','original_path','repo_path','size','sha256')} for f in files]
 (root/'大学资料'/'目录清单.json').write_text(json.dumps({'file_count':len(files),'files':listing},ensure_ascii=False,indent=2),encoding='utf-8')

class Publisher:
 def __init__(self,root):
  self.root=root.resolve();self.token=os.environ.get('GITHUB_TOKEN','')
  if os.environ.get('GITHUB_REPOSITORY')!=REPO or os.environ.get('GITHUB_REPOSITORY_ID')!=str(REPO_ID) or not self.token:raise RuntimeError('Runner repository identity differs')
  self.env=dict(os.environ)
  for k in ('GIT_CURL_VERBOSE','GIT_TRACE','GIT_TRACE_CURL'):self.env.pop(k,None)
  self.env['GIT_TERMINAL_PROMPT']='0'
  count=int(self.env.get('GIT_CONFIG_COUNT','0'))
  for key,value in [('credential.helper',''),('http.https://github.com/.extraheader','AUTHORIZATION: basic '+base64.b64encode(('x-access-token:'+self.token).encode()).decode())]:
   self.env[f'GIT_CONFIG_KEY_{count}']=key;self.env[f'GIT_CONFIG_VALUE_{count}']=value;count+=1
  self.env['GIT_CONFIG_COUNT']=str(count)
 def git(self,*args,capture=False):
  return subprocess.run(['git','-C',str(self.root),*args],env=self.env,check=True,text=True,stdout=subprocess.PIPE if capture else None).stdout
 def push(self):
  for attempt in range(20):
   try:self.git('push','origin','HEAD:main');return
   except subprocess.CalledProcessError:
    if attempt==19:raise
    print(f'Push interrupted; retrying the same commit ({attempt+1}/20)',flush=True);time.sleep(min(30,2**min(attempt,5)))
 def commit_paths(self,paths,message):
  if not paths:return
  self.git('add','--',*paths)
  result=subprocess.run(['git','-C',str(self.root),'diff','--cached','--quiet'],env=self.env)
  if result.returncode==0:return
  if result.returncode!=1:raise RuntimeError('Could not inspect staged files')
  self.git('commit','-m',message);self.push()
 def api(self,path):
  headers={'Authorization':'Bearer '+self.token,'Accept':'application/vnd.github+json','User-Agent':'985-school-directory-publisher'}
  for attempt in range(8):
   try:
    with urllib.request.urlopen(urllib.request.Request('https://api.github.com/'+path,headers=headers),timeout=90) as r:return json.load(r)
   except (OSError,ValueError):
    if attempt==7:raise
    time.sleep(min(60,2**attempt))
 def download(self,url,target,size,sha):
  parsed=urllib.parse.urlsplit(url)
  if parsed.scheme!='https' or parsed.hostname!='github.com' or not parsed.path.startswith('/'+REPO+'/releases/download/'):raise RuntimeError('Unexpected archive URL')
  for attempt in range(12):
   try:
    if target.exists() and target.stat().st_size==size and digest(target)==sha:return
    request=urllib.request.Request(url,headers={'User-Agent':'985-school-directory-publisher'})
    with urllib.request.urlopen(request,timeout=180) as r,target.open('wb') as out:
     for chunk in iter(lambda:r.read(4*1024**2),b''):out.write(chunk)
    if target.stat().st_size!=size or digest(target)!=sha:raise RuntimeError('Archive checksum differs')
    return
   except (OSError,RuntimeError):
    if attempt==11:raise
    print('Archive download interrupted; waiting before retry.',flush=True);time.sleep(min(60,2**attempt))

def publish(root):
 pub=Publisher(root)
 repo=pub.api('repos/'+REPO)
 if repo['id']!=REPO_ID or repo['private']:raise RuntimeError('Unexpected repository')
 manifest=json.loads((root/'文件清单.json').read_text('utf-8'));catalog=json.loads((root/'学校目录.json').read_text('utf-8'))
 schools,files=build_plan(manifest,catalog)
 pub.git('config','user.name','github-actions[bot]');pub.git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
 pub.git('config','core.compression','1');pub.git('config','pack.threads','2')
 release=pub.api('repos/'+REPO+f'/releases/{RELEASE_ID}')
 if release['draft']:raise RuntimeError('Original release is not published')
 assets={a['name']:a for a in release['assets']}
 # Source paths already tracked on a resumed job are verified and reused.
 with tempfile.TemporaryDirectory(prefix='course-archives-',dir=os.environ.get('RUNNER_TEMP')) as tmp:
  for course,(archive_name,count,archive_size,archive_sha) in EXPECTED.items():
   selected=[f for f in files if f['course']==course]
   missing=[]
   for f in selected:
    target=(root/f['repo_path']).resolve()
    if not target.is_relative_to((root/'大学资料').resolve()):raise RuntimeError('Target escapes material tree')
    if target.exists():
     if target.stat().st_size!=f['size'] or digest(target)!=f['sha256']:raise RuntimeError('An existing material differs; preserved')
    else:missing.append(f)
   if not missing:
    print(f'REUSED {course}: {count} files',flush=True);continue
   asset=assets.get(archive_name,{})
   if asset.get('size')!=archive_size or asset.get('digest')!='sha256:'+archive_sha:raise RuntimeError('Published archive identity differs')
   archive=pathlib.Path(tmp)/archive_name;pub.download(asset['browser_download_url'],archive,archive_size,archive_sha)
   pending=[];pending_bytes=0;done=count-len(missing)
   with zipfile.ZipFile(archive) as z:
    for f in missing:
     info=z.getinfo(f['source_member'])
     if info.file_size!=f['size']:raise RuntimeError('ZIP member size differs')
     target=root/f['repo_path'];target.parent.mkdir(parents=True,exist_ok=True)
     check=hashlib.sha256()
     with z.open(info) as source,target.open('wb') as out:
      for data in iter(lambda:source.read(1024**2),b''):out.write(data);check.update(data)
     if check.hexdigest()!=f['sha256']:raise RuntimeError('Material SHA256 differs')
     pending.append(f['repo_path']);pending_bytes+=f['size'];done+=1
     if pending_bytes>=BATCH_BYTES:
      pub.commit_paths(pending,f'Publish {course} material files ({done}/{count})');pending=[];pending_bytes=0
      print(f'PUSHED {course} {done}/{count}',flush=True)
   pub.commit_paths(pending,f'Complete {course} original files ({count})')
   archive.unlink();print(f'COURSE_COMPLETE {course}: {count}',flush=True)
 write_docs(root,schools,files,manifest)
 # Stage generated Markdown/JSON only; binary files have already been committed.
 docs=['README.md']+[p.relative_to(root).as_posix() for directory in ('大学资料','学科目录') for p in (root/directory).rglob('*') if p.is_file() and p.suffix in {'.md','.json'}]
 pub.commit_paths(docs,'Add browsable university, subject and detailed file directories')
 # core.quotePath=false is required for Unicode path output used below.
 tracked_raw=pub.git('-c','core.quotePath=false','ls-tree','-r','--name-only','HEAD','--','大学资料',capture=True)
 tracked=set(tracked_raw.splitlines())
 if any(f['repo_path'] not in tracked for f in files):raise RuntimeError('A verified material is missing from the committed tree')
 result={'published_files':len(files),'schools_with_files':len({f['school'] for f in files}),'school_records':len(schools),'total_bytes':TOTAL_BYTES,
         'university_directory':'https://github.com/'+REPO+'/tree/main/'+link('大学资料'),
         'subject_directory':'https://github.com/'+REPO+'/tree/main/'+link('学科目录')}
 print('DIRECTORIES_PUBLISHED '+json.dumps(result,ensure_ascii=False),flush=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=pathlib.Path,required=True);p.add_argument('--plan-only',action='store_true');p.add_argument('--preview',type=pathlib.Path);a=p.parse_args();root=a.root.resolve()
 if a.plan_only:
  manifest=json.loads((root/'文件清单.json').read_text('utf-8'));catalog=json.loads((root/'学校目录.json').read_text('utf-8'));schools,files=build_plan(manifest,catalog)
  if a.preview:
   a.preview.mkdir(parents=True,exist_ok=True);(a.preview/'README.md').write_text((root/'README.md').read_text('utf-8'),encoding='utf-8');write_docs(a.preview,schools,files,manifest)
  print(json.dumps({'files':len(files),'bytes':sum(f['size'] for f in files),'school_records':len(schools),'schools_with_files':len({f['school'] for f in files}),'unique_paths':len({f['repo_path'].casefold() for f in files}),'samples':[{'school':f['school'],'repo_path':f['repo_path']} for f in files[::1000]]},ensure_ascii=False));return
 publish(root)

if __name__=='__main__':main()
