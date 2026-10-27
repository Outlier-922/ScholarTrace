"""Standalone, deterministic plotting templates. This exact file ships in each ZIP.

No uploaded code is evaluated. Input CSV and JSON are treated exclusively as data.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import math
import re
import statistics
import textwrap
from collections import Counter,defaultdict
from pathlib import Path

MAX_BYTES=2*1024*1024
MISSING={'','na','n/a','null','none','nan'}
NUMBER=re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')
COLORS=['#287d8e','#e19b42','#6a994e','#b36b8d','#656bb1','#9b6b43','#398c77','#b55c56']


def number(value):
    value=str(value).strip()
    if not NUMBER.fullmatch(value):return None
    result=float(value)
    return result if math.isfinite(result) and abs(result)<=1e100 else None


def parse_csv(raw):
    if not raw or len(raw)>MAX_BYTES:raise ValueError('CSV需为非空且不超过2MB')
    if b'\x00' in raw and not raw.startswith((b'\xff\xfe',b'\xfe\xff')):raise ValueError('CSV包含无效控制字节')
    encodings=['utf-16'] if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','gb18030']
    text=None;encoding=None
    for candidate in encodings:
        try:text=raw.decode(candidate);encoding=candidate;break
        except UnicodeDecodeError:pass
    if text is None or '\x00' in text:raise ValueError('无法按UTF-8、UTF-16或GB18030读取CSV')
    try:delimiter=csv.Sniffer().sniff(text[:8192],delimiters=',;\t').delimiter
    except csv.Error:delimiter=','
    try:
        reader=csv.reader(io.StringIO(text,newline=''),delimiter=delimiter,strict=True)
        rows=[row for row in reader if row and any(cell.strip() for cell in row)]
    except csv.Error as error:raise ValueError('CSV引号或行结构无效') from error
    if len(rows)<2 or len(rows)>10001:raise ValueError('CSV需含表头及1—10000行数据')
    header=[cell.strip().lstrip('\ufeff') for cell in rows[0]]
    if not 2<=len(header)<=64 or len(set(header))!=len(header) or any(not cell or len(cell)>100 for cell in header):raise ValueError('列名需唯一且非空，支持2—64列，每列名最多100字')
    data=rows[1:]
    if any(len(row)!=len(header) or any(len(cell)>16000 for cell in row) for row in data):raise ValueError('CSV存在列数不一致或过长单元格')
    columns=[]
    for index,name in enumerate(header):
        values=[row[index].strip() for row in data]
        missing=sum(value.casefold() in MISSING for value in values)
        numeric=[number(value) for value in values if value.casefold() not in MISSING]
        valid=[value for value in numeric if value is not None]
        leading_zero=any(re.fullmatch(r'0\d+',value) for value in values)
        kind='numeric' if numeric and len(valid)==len(numeric) and not leading_zero else 'mixed' if valid else 'text'
        unit=re.search(r'[\[(]([^\])]{1,25})[\])]\s*$',name)
        columns.append({'name':name,'type':kind,'numeric_count':len(valid),'missing_count':missing,'invalid_numeric_count':len(numeric)-len(valid),
                        'unique_count':len(set(values)),'leading_zero_warning':leading_zero,'unit_hint':unit.group(1) if unit else None,
                        'minimum':min(valid) if valid else None,'maximum':max(valid) if valid else None})
    return {'sha256':hashlib.sha256(raw).hexdigest(),'encoding':encoding,'delimiter':delimiter,'headers':header,'rows':data,
            'row_count':len(data),'columns':columns,'duplicate_rows':sum(count-1 for count in Counter(tuple(row) for row in data).values() if count>1)}


def prepare_chart(dataset,spec):
    """Return all selected/aggregated observations plus an auditable transformation log."""
    kind=spec['chart_type'];header=dataset['headers'];x=spec['x'];ys=spec['y'];group=spec.get('group_by');repeat=spec.get('repetition')
    selected=list(dict.fromkeys([x]+ys+([group] if group else [])+([repeat] if repeat else [])))
    if any(name not in header for name in selected):raise ValueError('选择的列不在CSV中')
    if x in ys or group in ys or repeat in ys or group==x or (repeat and repeat in {x,group}):raise ValueError('横轴、指标、分组与重复实验列不能相互重叠')
    if kind in {'line','bar'} and len(ys)!=1:raise ValueError('折线/柱状每图选择一个指标，可通过分组比较多条序列')
    if kind=='scatter3d' and (len(ys)!=2 or spec['x_kind']!='numeric' or spec['aggregation']!='none'):raise ValueError('三维散点需数值横轴及两个数值指标，不作隐式聚合')
    if kind in {'radar','radar3d'}:
        if len(ys)<3:raise ValueError('雷达图至少需要3个指标')
        if group:raise ValueError('雷达图用横轴列区分对象，不再使用额外分组列')
        if spec['normalization'] not in {'minmax','bounds'} or any(spec['directions'].get(name) not in {'higher','lower'} for name in ys):raise ValueError('雷达图需明确归一化方式及每个指标方向')
    elif spec['normalization']!='none':raise ValueError('归一化仅用于雷达模板，其他图保留原始单位')
    if spec['error_bar']!='none' and (kind not in {'line','bar'} or spec['aggregation']!='mean' or not repeat):raise ValueError('误差条仅支持有重复实验列、明确求均值的折线/柱状图')
    index={name:header.index(name) for name in selected};records=[];dropped=[]
    for row_number,row in enumerate(dataset['rows'],1):
        raw={name:row[index[name]].strip() for name in selected}
        values={name:number(raw[name]) for name in ys}
        invalid=any(value is None for value in values.values()) or raw[x].casefold() in MISSING or (group and raw[group].casefold() in MISSING) or (repeat and raw[repeat].casefold() in MISSING)
        x_number=number(raw[x]) if spec['x_kind']=='numeric' else None
        if spec['x_kind']=='numeric' and x_number is None:invalid=True
        if invalid:
            if spec['missing_policy']=='drop':dropped.append(row_number);continue
            raise ValueError(f'第{row_number}个数据记录存在缺失/非有限数值；请选择明确删除这些记录或修正CSV，不会自动插补')
        records.append({'x':raw[x],'x_number':x_number,'group':raw[group] if group else 'all','repeat':raw[repeat] if repeat else None,'values':values,'row':row_number,'raw':raw})
    if not records:raise ValueError('处理后没有可绘制记录')
    if any(len(record['x'])>120 or len(record['group'])>120 for record in records):raise ValueError('对象/分组标签最多120字，请先在CSV中提供简短标识符，原始说明可另列保存')
    groups=list(dict.fromkeys(record['group'] for record in records));categories=list(dict.fromkeys(record['x'] for record in records))
    if kind=='line':
        # Preserve missing positions so lines cannot silently bridge excluded observations.
        categories=list(dict.fromkeys(row[header.index(x)].strip() for row in dataset['rows'] if row[header.index(x)].strip().casefold() not in MISSING and (spec['x_kind']!='numeric' or number(row[header.index(x)]) is not None)))
    if len(groups)>8:raise ValueError('每图最多8个分组，请先明确筛选数据')
    if kind in {'bar','radar','radar3d'} and len(categories)>30:raise ValueError('柱状/雷达模板最多30个横轴类别')
    if kind in {'radar','radar3d'} and len(categories)>8:raise ValueError('雷达图最多比较8个对象')
    if kind=='scatter3d' and len(records)>2000:raise ValueError('三维散点最多2000个点，不会静默抽样')
    if kind=='line' and len(records)>2000:raise ValueError('折线模板最多2000个观测记录')
    cells=defaultdict(list)
    for record in records:
        for metric in ys:cells[(record['x'],record['group'],metric)].append(record)
    points=[]
    for (category,group_value,metric),observations in cells.items():
        if repeat and len({item['repeat'] for item in observations})!=len(observations):raise ValueError('同一横轴/分组存在重复的实验标识；不能把重复记录当独立实验')
        if len(observations)>1 and spec['aggregation']=='none' and kind not in {'table','scatter3d'}:raise ValueError('同一横轴/分组有多条记录，请明确选择均值，或先提供唯一观测')
        batches=[observations] if spec['aggregation']=='mean' else [[item] for item in observations]
        for batch in batches:
            values=[item['values'][metric] for item in batch];n=len(values)
            sd=statistics.stdev(values) if n>=2 else None
            if spec['error_bar']!='none' and n<2:raise ValueError('每个误差条至少需要2次独立标识的重复观测')
            points.append({'x':category,'x_number':batch[0]['x_number'],'group':group_value,'metric':metric,'value':statistics.mean(values),'n':n,
                           'std':sd,'sem':sd/math.sqrt(n) if sd is not None else None,'rows':[item['row'] for item in batch]})
    normalizations={}
    if kind in {'radar','radar3d'}:
        for metric in ys:
            values=[point['value'] for point in points if point['metric']==metric]
            if spec['normalization']=='bounds':
                bound=spec.get('bounds',{}).get(metric)
                if not bound or len(bound)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in bound) or bound[0]>=bound[1]:raise ValueError('每个雷达指标需提供有效的下限/上限')
                low,high=bound
                if min(values)<low or max(values)>high:raise ValueError('观测值超出设定归一化边界，不会自动裁剪')
            else:low,high=min(values),max(values)
            normalizations[metric]={'lower':low,'upper':high,'direction':spec['directions'][metric],'constant':high==low}
            for point in points:
                if point['metric']!=metric:continue
                value=.5 if high==low else (point['value']-low)/(high-low)
                point['normalized']=1-value if spec['directions'][metric]=='lower' else value
    if kind in {'radar','radar3d'} and any(len([p for p in points if p['x']==category])!=len(ys) for category in categories):raise ValueError('雷达图每个对象必须有完整的指标向量')
    if spec['x_kind']=='numeric':categories.sort(key=lambda value:number(value))
    if spec['x_kind']=='numeric' and kind=='line' and len({number(value) for value in categories})!=len(categories):raise ValueError('横轴存在不同文本对应同一数值，请先统一数值表示后再绘图')
    statistics_by_metric={metric:{'count':len([p for p in points if p['metric']==metric]),'minimum':min(p['value'] for p in points if p['metric']==metric),
                         'maximum':max(p['value'] for p in points if p['metric']==metric),'mean':statistics.mean(p['value'] for p in points if p['metric']==metric)} for metric in ys}
    warnings=[]
    if dropped:warnings.append(f'按用户选择删除{len(dropped)}个数据记录，原始文件与序号保留；没有插补。')
    if dataset['duplicate_rows']:warnings.append(f'原CSV有{dataset["duplicate_rows"]}个重复记录，未自动去重。')
    if any(not spec['units'].get(metric) for metric in ys):warnings.append('部分指标未提供单位，保留原数值，不推断百分比或量纲。')
    if spec['aggregation']=='mean' and not repeat:warnings.append('没有提供重复实验标识，均值仅是记录聚合，不证明实验独立性。')
    if kind=='radar3d':warnings.append('z轴是对象类别分层，不是新的性能指标；各半径为明确方向的归一化值。')
    if normalizations and any(item['constant'] for item in normalizations.values()):warnings.append('常量指标全部记为0.5，仅表示相同，不产生该指标的优劣排名。')
    description={'table':'数据表','line':'折线图','bar':'柱状图','radar':'雷达图','radar3d':'分层三维雷达图','scatter3d':'三维散点图'}[kind]
    caption=f'{spec["title"]}。{description}基于上传CSV的{len(dataset["rows"])}个记录，使用{len(records)}个，明确排除{len(dropped)}个；'
    caption+='按横轴/分组求均值。' if spec['aggregation']=='mean' else '未进行均值聚合。'
    caption+=' '.join(f'{metric}（{spec["units"].get(metric) or "单位未提供"}）的绘制值范围为{values["minimum"]:.6g}—{values["maximum"]:.6g}。' for metric,values in statistics_by_metric.items())
    if spec['error_bar']!='none':caption+=f'误差条表示{"样本标准差（ddof=1）" if spec["error_bar"]=="std" else "均值标准误（样本标准差/√n）"}，不代表置信区间或显著性。'
    if normalizations:caption+='半径按'+('当前选定对象的最小—最大值' if spec['normalization']=='minmax' else '明确给定边界')+'缩放，指标方向见配置；不据此给出综合排名。'
    caption+='图示不构成因果、统计显著性或实验已完成的证明。'
    if dataset.get('data_status')=='synthetic_demo':caption='【自建演示数据，非实验结果】'+caption
    return {'spec':spec,'sha256':dataset['sha256'],'records':records,'points':points,'groups':groups,'categories':categories,'normalizations':normalizations,
            'statistics':statistics_by_metric,'used_records':len(records),'dropped_rows':dropped,'warnings':warnings,'caption':caption,'data_status':dataset.get('data_status','user_data')}


def plotting_style():
    from matplotlib import font_manager
    chosen='DejaVu Sans'
    for name in ['Microsoft YaHei','Noto Sans CJK SC','SimHei','DejaVu Sans']:
        try:font_manager.findfont(font_manager.FontProperties(family=name),fallback_to_default=False);chosen=name;break
        except ValueError:pass
    return {'font.family':chosen,'axes.unicode_minus':False,'text.usetex':False,'text.parse_math':False,'svg.fonttype':'path','pdf.fonttype':42,
            'font.size':10,'axes.titlesize':14,'axes.labelsize':11,'axes.spines.top':False,'axes.spines.right':False,'savefig.facecolor':'white','figure.facecolor':'white'}


def render_chart(prepared,folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    spec=prepared['spec'];kind=spec['chart_type'];ys=spec['y'];points=prepared['points'];categories=prepared['categories']
    with plt.rc_context(plotting_style()):
        fig=plt.figure(figsize=(9,5.8),layout='constrained')
        ax=fig.add_subplot(111,projection='polar' if kind=='radar' else '3d' if kind in {'radar3d','scatter3d'} else None)
        if kind=='table':
            ax.axis('off');columns=[spec['x']]+(['group'] if spec.get('group_by') else [])+ys+(['n'] if spec['aggregation']=='mean' else [])
            rows=[]
            if spec['aggregation']=='none':
                for record in prepared['records']:rows.append([record['x']]+([record['group']] if spec.get('group_by') else [])+[f'{record["values"][metric]:.6g}' for metric in ys])
            else:
                for category in categories:
                    for group in prepared['groups']:
                        found=[p for p in points if p['x']==category and p['group']==group]
                        if found:rows.append([category]+([group] if spec.get('group_by') else [])+[f'{next(p["value"] for p in found if p["metric"]==metric):.6g}' for metric in ys]+[str(found[0]['n'])])
            table=ax.table(cellText=rows[:30],colLabels=columns,loc='center',cellLoc='center');table.auto_set_font_size(False);table.set_fontsize(8);table.scale(1,1.5)
            for (row,column),cell in table.get_celld().items():
                cell.set_edgecolor('#dce4dd')
                if row==0:cell.set_facecolor('#eaf0e8');cell.set_text_props(weight='bold')
            if len(rows)>30:ax.text(.5,0,f'仅预览前30行，共{len(rows)}行；完整数据见processed.csv',ha='center',transform=ax.transAxes,fontsize=9)
        elif kind in {'line','bar'}:
            metric=ys[0];groups=prepared['groups'];width=.75/max(1,len(groups))
            for index,group in enumerate(groups):
                found={p['x']:p for p in points if p['metric']==metric and p['group']==group}
                available=categories if kind=='line' else [category for category in categories if category in found]
                positions=[number(category) if spec['x_kind']=='numeric' and kind=='line' else categories.index(category) for category in available]
                values=[found[category]['value'] if category in found else math.nan for category in available]
                errors=[found[category][spec['error_bar']] if category in found else math.nan for category in available] if spec['error_bar']!='none' else None
                label=group if spec.get('group_by') else metric
                if kind=='line':ax.errorbar(positions,values,yerr=errors,label=label,color=COLORS[index%8],marker='o',markersize=4,linewidth=1.7,capsize=3)
                else:ax.bar([position+(index-(len(groups)-1)/2)*width for position in positions],values,width=width,yerr=errors,label=label,color=COLORS[index%8],capsize=3)
            if spec['x_kind']!='numeric' or kind=='bar':ax.set_xticks(range(len(categories)),[textwrap.fill(value,18) for value in categories],rotation=25 if len(categories)>6 else 0)
            else:ax.xaxis.set_major_locator(MaxNLocator(nbins=8))
            ax.set_xlabel(spec['x_label'] or spec['x']);ax.set_ylabel(spec['y_label'] or metric+(f' ({spec["units"][metric]})' if spec['units'].get(metric) else ''))
            if kind=='bar':ax.axhline(0,color='#758075',linewidth=.8)
            ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
            if spec.get('group_by'):ax.legend(frameon=False)
        elif kind in {'radar','radar3d'}:
            angles=[2*math.pi*i/len(ys) for i in range(len(ys))];closed=angles+[angles[0]]
            for index,category in enumerate(categories):
                found={p['metric']:p for p in points if p['x']==category};values=[found[metric]['normalized'] for metric in ys];values+=values[:1]
                if kind=='radar':ax.plot(closed,values,label=category,color=COLORS[index%8],linewidth=1.6,marker='o',markersize=3);ax.fill(closed,values,color=COLORS[index%8],alpha=.08)
                else:
                    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
                    vertices=[(value*math.cos(angle),value*math.sin(angle),index) for angle,value in zip(closed,values)]
                    ax.plot([p[0] for p in vertices],[p[1] for p in vertices],[p[2] for p in vertices],color=COLORS[index%8],label=category,linewidth=1.8)
                    ax.add_collection3d(Poly3DCollection([vertices],facecolor=COLORS[index%8],alpha=.13,edgecolor=COLORS[index%8]))
            labels=[textwrap.fill(metric,20)+(' ↑' if spec['directions'][metric]=='higher' else ' ↓') for metric in ys]
            if kind=='radar':ax.set_theta_offset(math.pi/2);ax.set_theta_direction(-1);ax.set_xticks(angles,labels);ax.set_ylim(0,1);ax.set_yticks([0,.25,.5,.75,1]);ax.legend(loc='upper left',bbox_to_anchor=(1.07,1),frameon=False)
            else:
                ax.set(xlim=(-1.3,1.3),ylim=(-1.3,1.3),zlim=(-.4,max(1,len(categories)-1)+.4),xlabel='Normalized radius · x',ylabel='Normalized radius · y',zlabel='对象类别分层')
                ax.set_zticks(range(len(categories)),categories)
                for angle,label in zip(angles,labels):ax.text(1.15*math.cos(angle),1.15*math.sin(angle),0,label,fontsize=8)
                ax.view_init(elev=24,azim=35)
        elif kind=='scatter3d':
            for index,group in enumerate(prepared['groups']):
                records=[record for record in prepared['records'] if record['group']==group]
                ax.scatter([r['x_number'] for r in records],[r['values'][ys[0]] for r in records],[r['values'][ys[1]] for r in records],color=COLORS[index%8],label=group,s=25,alpha=.85)
            def axis_label(metric):return metric+(f' ({spec["units"][metric]})' if spec['units'].get(metric) else '')
            ax.set_xlabel(spec['x_label'] or axis_label(spec['x']));ax.set_ylabel(axis_label(ys[0]));ax.set_zlabel(axis_label(ys[1]))
            if spec.get('group_by'):ax.legend(frameon=False)
        ax.set_title(spec['title'],pad=20 if kind in {'radar','radar3d'} else 14)
        if prepared.get('data_status')=='synthetic_demo':fig.supxlabel('自建演示数据 · SYNTHETIC DEMO · 非实验结果',fontsize=9,color='#a15f24')
        for extension in ['png','svg','pdf']:fig.savefig(folder/('figure.'+extension),dpi=180,bbox_inches='tight')
        plt.close(fig)
    return {'font':plotting_style()['font.family'],'matplotlib':matplotlib.__version__}


def safe_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def write_processed(prepared,path):
    with Path(path).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['x','group','metric','value','n','std','sem','normalized','source_record_numbers'])
        for point in prepared['points']:writer.writerow([safe_cell(point['x']),safe_cell(point['group']),safe_cell(point['metric']),point['value'],point['n'],point['std'],point['sem'],point.get('normalized'),','.join(map(str,point['rows']))])


def diagram_layout(spec):
    nodes=spec['nodes'];ids={node['id'] for node in nodes};degree={identity:0 for identity in ids};outgoing=defaultdict(list)
    for edge in spec['edges']:
        if edge['source'] not in ids or edge['target'] not in ids:raise ValueError('架构图边必须连接已声明节点')
        degree[edge['target']]+=1;outgoing[edge['source']].append(edge['target'])
    queue=sorted(identity for identity,value in degree.items() if value==0);levels={identity:0 for identity in ids};visited=0
    while queue:
        current=queue.pop(0);visited+=1
        for target in outgoing[current]:
            levels[target]=max(levels[target],levels[current]+1);degree[target]-=1
            if degree[target]==0:queue.append(target)
    if visited<len(nodes):
        radius=max(3,len(nodes)*.5)
        return {node['id']:(math.cos(2*math.pi*i/len(nodes))*radius,math.sin(2*math.pi*i/len(nodes))*radius*.72) for i,node in enumerate(nodes)},'cycle_ring'
    groups=defaultdict(list)
    for node in nodes:groups[levels[node['id']]].append(node['id'])
    maximum=max(groups);positions={}
    if maximum>4:
        offset=0
        for band in range(maximum//5+1):
            band_levels=range(band*5,min(maximum+1,(band+1)*5))
            size=max(len(groups[level]) for level in band_levels)
            for level in band_levels:
                column=level%5 if band%2==0 else 4-level%5
                for index,identity in enumerate(groups[level]):positions[identity]=(column*2.8,-offset+(index-(len(groups[level])-1)/2)*1.8)
            offset+=size*1.8+1.2
        return positions,'wrapped_dag'
    for level,group in groups.items():
        for index,identity in enumerate(group):positions[identity]=(level*2.8,(index-(len(group)-1)/2)*1.8)
    return positions,'layered_dag'


def mermaid_source(spec):
    def label(text):return text.replace('&','&amp;').replace('"','&quot;').replace('<','&lt;').replace('>','&gt;').replace('\n',' ')
    lines=['flowchart LR']
    for node in spec['nodes']:lines.append(f'  {node["id"]}["{label(node["label"])}"]')
    for edge in spec['edges']:lines.append(f'  {edge["source"]} --> {edge["target"]}')
    return '\n'.join(lines)+'\n'


def render_diagram(spec,folder):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
    positions,layout=diagram_layout(spec);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    width=max(x for x,y in positions.values())-min(x for x,y in positions.values());height=max(y for x,y in positions.values())-min(y for x,y in positions.values())
    with plt.rc_context(plotting_style()):
        fig,ax=plt.subplots(figsize=(min(16,max(8,width*1.2)),min(12,max(4.5,height*1.3))),layout='constrained');ax.axis('off')
        boxes={}
        for node in spec['nodes']:
            x,y=positions[node['id']];box=FancyBboxPatch((x-.95,y-.38),1.9,.76,boxstyle='round,pad=0.06',facecolor='#edf3e9',edgecolor='#7b9870',linewidth=1.2);ax.add_patch(box)
            boxes[node['id']]=box
            ax.text(x,y,textwrap.fill(node['label'],12),ha='center',va='center',fontsize=10)
        for edge in spec['edges']:
            a=positions[edge['source']];b=positions[edge['target']]
            ax.add_patch(FancyArrowPatch(a,b,patchA=boxes[edge['source']],patchB=boxes[edge['target']],arrowstyle='-|>',mutation_scale=15,color='#80947c',linewidth=1.4,shrinkA=5,shrinkB=5,connectionstyle='arc3,rad=0.05',zorder=2))
        ax.set_xlim(min(x for x,y in positions.values())-1.35,max(x for x,y in positions.values())+1.35);ax.set_ylim(min(y for x,y in positions.values())-.9,max(y for x,y in positions.values())+.9)
        ax.set_title(spec['title']+'\n设计草图，待实施验证',fontsize=13,pad=16)
        for extension in ['png','svg','pdf']:fig.savefig(folder/('figure.'+extension),dpi=180,bbox_inches='tight')
        plt.close(fig)
    (folder/'architecture.mmd').write_text(mermaid_source(spec),encoding='utf-8')
    return {'layout':layout,'font':plotting_style()['font.family'],'matplotlib':matplotlib.__version__}


if __name__=='__main__':
    root=Path(__file__).resolve().parent;manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    if manifest['kind']=='chart':
        raw=(root/'input.csv').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest['sha256']:raise SystemExit('Input hash changed; refusing silent reproduction')
        parsed=parse_csv(raw);parsed['data_status']=manifest.get('data_status','user_data')
        prepared=prepare_chart(parsed,manifest['spec']);render_chart(prepared,root/'reproduced');write_processed(prepared,root/'reproduced'/'processed.csv')
        (root/'reproduced'/'caption.txt').write_text(prepared['caption'],encoding='utf-8')
    else:render_diagram(manifest['spec'],root/'reproduced')
    print('Reproduced controlled template outputs in ./reproduced')
