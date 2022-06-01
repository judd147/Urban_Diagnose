# -*- coding: utf-8 -*-
"""
蕾奥城市中心体系分析软件V1.0
Last Edit 5/31/2022
@author: zhangliyao
"""
import gc
import requests
import streamlit as st
import streamlit_authenticator as stauth
import plotly.express as px
import pandas as pd
import geopandas as gpd
import libpysal
import json
import yaml
from yaml.loader import SafeLoader
from shapely.geometry import Polygon
from numpy import log as ln
from pysal.explore.esda import G_Local

def main():    
    st.sidebar.title("导航")
    apps = st.sidebar.multiselect("选择分析模块", ["城市中心体系分析"])
    url = 'https://raw.githubusercontent.com/judd147/Urban_Diagnose/main/user_config.yaml'
    file = requests.get(url)
    config = yaml.load(file.text, Loader=SafeLoader)
    
    authenticator = stauth.Authenticate(
    config['credentials']['names'],
    config['credentials']['usernames'],
    config['credentials']['passwords'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days'])
    name, authentication_status, username = authenticator.login('Login', 'main')
    
    if st.session_state["authentication_status"]:
        authenticator.logout('Logout', 'main')
        st.write(f'Welcome *{st.session_state["name"]}*')
        if apps.__contains__("城市中心体系分析"):
            urban_center_analysis()
    elif st.session_state["authentication_status"] == False:
        st.error('Username/password is incorrect')
    elif st.session_state["authentication_status"] == None:
        st.warning('Please enter your username and password')
   
def urban_center_analysis():
    #数据输入
    st.header("蕾奥城市中心体系分析软件V1.0")
    st.caption("基于POI数据的城市中心范围、等级、功能识别")
    mode = st.radio("选择运行模式", ["常规", "可视化"], help='常规模式指从数据输入到可视化的全流程，可视化指上传结果文件进行可视化')
    
    if mode == '常规':
        with st.form(key='urban_center_analysis'):
            #文件设置
            geo = st.file_uploader("上传范围", type='geojson', key='urban_center_analysis')
            pois = st.file_uploader("上传POI数据", type='csv', key='urban_center_analysis', accept_multiple_files=True)

            #参数设置
            cellsize = st.number_input("网格大小", min_value=50, max_value=1000, value=500, help="根据分析范围划分网格，默认值为500米")
            geo_relation = st.radio("空间邻接算法", ["Queen", "Rook"], help='Queen为共顶点和共边邻接，Rook为共边邻接')      
            p_value = st.number_input("显著性水平", min_value=0.01, max_value=0.05, value=0.01, help="用于确定热点区范围，默认值0.01")
            threshold = st.text_input("去噪阈值", value='0.006', help="用于去除POI总数较少的噪点，默认值0.006")
            func_threshold = st.number_input("区位熵阈值", min_value=1.15, max_value=1.5, value=1.3, help="用于判断是否为综合功能中心，默认值1.3")
            
            preview = st.checkbox("数据预览", value=False, key='urban_center_analysis')
            run = st.form_submit_button(label='运行')
            
        if run:
            gc.enable()
            with st.spinner("正在读取数据..."):
                dfy = gpd.read_file(geo) #输入范围
                dfy.to_crs(epsg=4547, inplace=True) #转投影坐标
                netfish = create_grid(dfy, cellsize) #根据输入范围创建网格
                
                #读取合并所有类别数据
                df = read_file(pois, dfy)
                del pois

            if preview:
                st.write(df.head())
    
            with st.spinner("正在处理POI数据..."):
                df.dropna(subset=['name'], axis=0, how='any', inplace=True) #检查名称是否为空
                df.drop_duplicates(subset=['name','address'], keep='first', inplace=True) #按名称+地址去重
                df[['一级分类','二级分类','三级分类']] = df['type'].str.split(';', expand=True, n=2) #增加类别字段                
                df.drop(columns=['address','type'], inplace=True)
                
                st.write('ready to play big')
                df = reclassify(df) #重分类
            st.success('处理完成！共有'+str(len(df))+'条POI数据')
    
            with st.spinner("正在进行空间计算..."):
                #渔网空间相交
                dfo = gpd.sjoin(netfish, df, op='contains') #POI数据与渔网空间相交              
                #指数计算
                result = calc_index(dfo)
                #指数结果合并geometry
                dfo_join = dfo.drop_duplicates(subset=['index','geometry'], keep='first')
                df_result = pd.merge(result, dfo_join[['index', 'geometry']], on='index', how='inner')
                #中心相关计算
                center_result, polygons = explore_center(df_result, geo_relation, p_value, float(threshold))
                #合并功能得到最终结果
                final_result, entropy = func_decider(dfo, center_result, polygons, func_threshold)
            st.success('运行成功！')    
            #导出结果
            name = parse_path(geo.name)
            csv = convert_df(final_result)
            st.download_button(
                 label="下载结果文件",
                 data=csv,
                 file_name='中心分析结果_'+name+'.csv',
                 mime='csv',
            )
            show_plot(final_result, dfy)
            
    elif mode == '可视化':
        data = st.file_uploader("上传分析结果", type='csv', key='replot')
        geo = st.file_uploader("上传范围", type='geojson', key='replot')
        
        if data and geo: 
            df = pd.read_csv(data, encoding = "gb18030")
            dfy = gpd.read_file(geo) #输入范围
            dfy.to_crs(epsg=4547, inplace=True) #转投影坐标
            show_plot(df, dfy, 1)

def show_plot(final_result, dfy, signal=0):
    """
    Goal: 在线可视化
    Args:
        final_result: 用于可视化的数据
        signal: 可选参数，默认为0；如果传入值为1，需要重新处理为geodataframe
    Returns: None
    """
    st.subheader('可视化参数设置')
    with st.form(key='visualization'):
        key_option = st.selectbox("Mapbox Key", options=['默认样式1首选','默认样式1备选','默认样式2'], help="用于加载Mapbox底图")
        
        col1, col2, col3 = st.columns(3)
        with col1:
            variable = st.selectbox('可视化类型', options=['等级','功能'], key='variable')
    
        with col2:
            basemap = st.selectbox('底图样式', options=['默认样式1','默认样式2','light','dark','streets','outdoors','satellite','carto-positron','carto-darkmatter','open-street-map','stamen-terrain','stamen-toner','stamen-watercolor'], key='basemap', help='默认样式需和Mapbox Key对应')
    
        with col3:
            alpha = st.number_input("透明度", min_value=0.0, max_value=1.0, value=1.0)
            
        col5, col6, col7, col8 = st.columns(4)
        with col5:
            line_color = st.color_picker('范围边界颜色', '#000000')
        
        with col6:
            color1 = st.color_picker('主中心填充色', '#BB1818')
            
        with col7:
            color2 = st.color_picker('次中心填充色', '#182FBB')
            
        with col8:
            color3 = st.color_picker('组团填充色', '#0DF115')
        
        custom_color = st.checkbox("使用自定义配色", value=False, help='不勾选该项，将使用系统默认配色；范围边界颜色不包括在内，因为修改后会立即生效')
        run = st.form_submit_button(label='应用')
    
    if run:
        #数据重新处理
        if signal == 1:
            geometry = gpd.GeoSeries.from_wkt(final_result['geometry'])
            final_result = gpd.GeoDataFrame(final_result, geometry=geometry, crs=4547)
            
        #Mapbox Key Match
        if key_option == '默认样式1首选':
            key = 'pk.eyJ1IjoibW9leDEwMDIzNiIsImEiOiJjbDF1ZW1oYmYybXAyM2NvMmczNmRlOXptIn0.HAW1OjKgMO_cBdSWVvMKjg'
            style = 'mapbox://styles/moex100236/cl1ucnkj3001y14o6m8ynk3w2'
        elif key_option == '默认样式1备选':
            key = 'pk.eyJ1IjoibGF5b3V0LTAxIiwiYSI6ImNsMXUyZDQ5cTI0cGozam10cHkzcGNyaHcifQ.TTnruNf6v0sceYuwcoa-_Q'
            style = 'mapbox://styles/layout-01/cl1u2kmvs009y14o981ct3hru'
        elif key_option == '默认样式2':
            key = 'pk.eyJ1IjoianVueWFvLXhpYW8iLCJhIjoiY2o3Y29zMGRoMDBqMTM0bXR5d2VlenpycSJ9.i0SXqgJ7Bhf8UhJ04Ygq_A'
            style = 'mapbox://styles/junyao-xiao/ckvjgucwz13sj14pf5mf6wlq9'

        #转为WGS84坐标
        dfy.to_crs(epsg=4326,inplace=True)
        final_result.to_crs(epsg=4326,inplace=True)
        
        #设置标题和自定义颜色
        cmap = None
        labels = {"level": "等级", "function": "功能"}
        if variable == '等级':
            title = '中心等级分布图'
            var = 'level'
            if custom_color:
                cmap = {'主中心': color1, '次中心': color2, '组团': color3}
        elif variable == '功能':
            title = '中心功能分布图'
            var = 'function'
             
        #可视化
        if basemap.__contains__('默认'):
            fig = px.choropleth_mapbox(final_result,
                           geojson=final_result.geometry,
                           locations=final_result.index,
                           color=var,
                           color_discrete_map=cmap,
                           hover_data=['area','num_poi','level','function'],
                           labels=labels,
                           center={"lat": 22.6, "lon": 114},
                           mapbox_style='white-bg',
                           opacity=alpha,
                           title=title,
                           zoom=8.5)
            fig.update_layout(mapbox={"accesstoken": key, 'style': style,"layers": [
                    {
                        "source": json.loads(dfy.geometry.to_json()), #绘制范围
                        "below": "traces",
                        "type": "line",
                        "color": line_color,
                        "line": {"width": 1.5},
                    }
                ]
            })

        else: 
            fig = px.choropleth_mapbox(final_result,
                           geojson=final_result.geometry,
                           locations=final_result.index,
                           color=var,
                           color_discrete_map=cmap,
                           hover_data=['area','num_poi','level','function'],
                           labels=labels,
                           center={"lat": 22.6, "lon": 114},
                           mapbox_style=basemap,
                           opacity=alpha,
                           title=title,
                           zoom=8.5)
            fig.update_layout(mapbox={"accesstoken": key, "layers": [
                    {
                        "source": json.loads(dfy.geometry.to_json()), #绘制范围
                        "below": "traces",
                        "type": "line",
                        "color": line_color,
                        "line": {"width": 1.5},
                    }
                ]
            })

        st.plotly_chart(fig, use_container_width=True)

def read_file(pois, dfy):
    frames = []
    for poi in pois:
        df = pd.read_csv(poi, usecols=['id','name','address','type','wgslng','wgslat'], converters = {'id': str, 'name': str, 'address': str, 'type': str, 'wgslng': float, 'wgslat': float}, encoding='gb18030')
        #筛选范围内数据
        df = poi_intersect(df, dfy)       
        frames.append(df)
    df_final = pd.concat(frames)
    return df_final         

@st.cache()
def convert_df(df):
    return df.to_csv(encoding = "gb18030", index=False)  
      
def parse_path(path):
    """
    Goal: 从文件路径提取范围名称
    Returns: String
    """
    if path is not None:
        path = path.split('\\')
        temp = path[len(path)-1]
        temp = temp.split('.')
        name = temp[0]
        return name

def create_grid(dfy, cellsize):
    '''
    Goals: 创建渔网
    Args: 
        dfy[geodataframe]: 分析范围
        cellsize[int]: 网格大小，单位：米
    Returns:
        netfish[geodataframe]: 渔网范围
    '''
    sys_proj = '4547'
    
    coord1 = (dfy['geometry'].total_bounds[0]-100, dfy['geometry'].total_bounds[3]+100)
    coord3 = (dfy['geometry'].total_bounds[2]+100, dfy['geometry'].total_bounds[1]-100)
    coord2 = (coord3[0],coord1[1])
    coord4 = (coord1[0],coord3[1])
    rectangle = Polygon([coord1,coord2,coord3,coord4])
    rectangle = gpd.GeoDataFrame([rectangle],columns=['geometry'])
    rectangle = rectangle.set_crs(epsg=sys_proj)
    coords = rectangle['geometry'].bounds.values[0]
    loc_all = '{},{},{},{}'.format(coords[0],coords[3],coords[2],coords[1])
    nets = lng_lat(loc_all, cellsize)
    netfish = gpd.GeoDataFrame([getPolygon(i[0],i[1]) for i in nets],columns=['geometry'])
    netfish = netfish.set_crs(epsg=sys_proj)
    netfish = netfish.reset_index()
    return netfish
        
#切割渔网所需函数
def lng_lat(loc_all, div):
    #提取经纬度
    lngH = float(loc_all.split(',')[2])
    lngL = float(loc_all.split(',')[0])
    latH = float(loc_all.split(',')[1])
    latL = float(loc_all.split(',')[3])
    #按照一个数值切割纬度
    lat_ls = [str(latH)]
    while latH - latL > 0:
        latH = latH - div
        lat_ls.append('{:.2f}'.format(latH))
    #按照一个数值切割经度
    lng_ls = [str(lngH)]
    while lngH - lngL > 0:
        lngH = lngH - div
        lng_ls.append('{:.2f}'.format(lngH))
    #获取经纬度列表
    lat = lat_ls
    lng = sorted(lng_ls)
    #组合经纬度成为坐标
    lst = []
    for a in lat:
        for n in lng:
            lst.append('{},{}'.format(n, a))
    #创建一个嵌套列表，便于后面进行坐标串组合
    lst1 = []
    for i in range(len(lat)):
        lst1.append(lst[i * len(lng):(i + 1) * len(lng)])
    #坐标串组合
    lsta = []
    for a in range(0, len(lat) - 1):
        for n in range(0, len(lng) - 1):
            coords = (float(lst1[a][n].split(',')[0]),float(lst1[a][n].split(',')[1])),\
                     (float(lst1[a+1][n+1].split(',')[0]),float(lst1[a+1][n+1].split(',')[1]))
            lsta.append(coords)
    return lsta

def getPolygon(coord1,coord3):
    coord1 = coord1
    coord3 = coord3
    coord2 = (coord3[0],coord1[1])
    coord4 = (coord1[0],coord3[1])
    rectangle = Polygon([coord1,coord2,coord3,coord4])
    return rectangle

@st.cache(max_entries=10, ttl=3600)
def reclassify(df):
    '''
    基于原始数据按首两个分号隔开生成三级分类，并按规则重新划分大类、中类、小类
    同时满足多项规则的，以最后满足的规则为准（覆盖前面的分类）     
    '''
    df = df[(df['一级分类'].str.contains("事件活动|交通设施服务|公共设施|地名地址信息|室内设施|摩托车服务|汽车服务|汽车维修|汽车销售|通行设施|道路附属设施")==False)]
    
    rule_1 = (df['一级分类']=='公司企业')
    df.loc[rule_1, ['大类','中类']] = ['工业功能','工业生产功能']
    
    rule_1_1 = (df['二级分类'].str.contains('公司|公司企业|知名企业'))
    df.loc[rule_1_1, ['小类']] = ['公司企业']
    
    rule_1_2 = (df['二级分类']=='工厂')
    df.loc[rule_1_2, ['小类']] = ['工厂']

    rule_2 = (df['一级分类']=='住宿服务')
    df.loc[rule_2, ['大类','中类']] = ['商业服务业功能','住宿服务功能']
    
    rule_2_1 = (df['二级分类']=='住宿服务相关') & (df['name'].str.contains('酒店|宾馆'))
    df.loc[rule_2_1, ['小类']] = ['经济型酒店']
    
    rule_2_2_1 = (df['二级分类']=='宾馆酒店') & (df['三级分类'].str.contains('星级宾馆'))
    df.loc[rule_2_2_1, ['小类']] = ['星级酒店']
    
    rule_2_2_2 = (df['二级分类']=='宾馆酒店') & (df['三级分类'].str.contains('星级宾馆')==False)
    df.loc[rule_2_2_2, ['小类']] = ['经济型酒店']
    
    rule_2_3 = (df['二级分类']=='旅馆招待所')
    df.loc[rule_2_3, ['小类']] = ['招待所']
    
    rule_2_4 = (df['一级分类']=='住宿服务') & (df['name'].str.contains('招待所'))
    df.loc[rule_2_4, ['小类']] = ['招待所']
    
    rule_2_5 = (df['一级分类']=='住宿服务') & (df['小类'].isnull())
    df.loc[rule_2_5, ['小类']] = ['招待所']

    rule_3 = (df['一级分类']=='体育休闲服务')
    df.loc[rule_3, ['大类','中类']] = ['商业服务业功能','休闲娱乐功能']
    
    rule_3_1_1 = (df['二级分类']=='体育休闲服务场所') & (df['name'].str.contains('健身|球|游泳|跆拳道'))
    df.loc[rule_3_1_1, ['小类']] = ['运动场所']
    
    rule_3_1_2 = (df['二级分类']=='体育休闲服务场所') & (df['name'].str.contains('影'))
    df.loc[rule_3_1_2, ['小类']] = ['影剧院']
    
    rule_3_1_3 = (df['二级分类']=='体育休闲服务场所') & (df['小类'].isnull())
    df.loc[rule_3_1_3, ['小类']] = ['娱乐场所']
    
    rule_3_2 = (df['二级分类'].str.contains('运动场馆|高尔夫相关'))
    df.loc[rule_3_2, ['小类']] = ['运动场所']
    
    rule_3_3 = (df['二级分类']=='影剧院')
    df.loc[rule_3_3, ['小类']] = ['影剧院']
    
    rule_3_4 = (df['二级分类']=='娱乐场所')
    df.loc[rule_3_4, ['小类']] = ['娱乐场所']

    rule_4 = (df['一级分类']=='医疗保健服务')
    df.loc[rule_4, ['大类','中类']] = ['公共管理服务功能','医疗健康功能']
    
    rule_4_1 = (df['二级分类'].str.contains('综合医院|急救中心'))
    df.loc[rule_4_1, ['小类']] = ['医院']
    
    rule_4_2_1 = (df['二级分类']=='专科医院') & (df['三级分类'].str.contains('诊所')==False)
    df.loc[rule_4_2_1, ['小类']] = ['医院']
    
    rule_4_2_2 = (df['二级分类']=='专科医院') & (df['三级分类'].str.contains('诊所'))
    df.loc[rule_4_2_2, ['小类']] = ['诊所']
    
    rule_4_3 = (df['二级分类']=='诊所')
    df.loc[rule_4_3, ['小类']] = ['诊所']
    
    rule_4_4 = (df['二级分类']=='疾病预防机构')
    df.loc[rule_4_4, ['小类']] = ['疾病预防中心']
    
    rule_4_5_0 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('医院'))
    df.loc[rule_4_5_0, ['小类']] = ['医院']
    
    rule_4_5_1 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('体检'))
    df.loc[rule_4_5_1, ['小类']] = ['专业体检机构']
    
    rule_4_5_2 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('社区'))
    df.loc[rule_4_5_2, ['小类']] = ['社区服务站']
    
    rule_4_5_3 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('预防'))
    df.loc[rule_4_5_3, ['小类']] = ['疾病预防中心']
    
    rule_4_5_4 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('诊所'))
    df.loc[rule_4_5_4, ['小类']] = ['诊所']
    
    rule_4_5_5 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('药房|药店'))
    df.loc[rule_4_5_5, ['小类']] = ['药店']
    
    rule_4_5_6 = (df['二级分类']=='医疗保健服务场所') & (df['name'].str.contains('宠物|动物'))
    df.loc[rule_4_5_6, ['小类']] = ['动物医院']
    
    rule_4_6 = (df['二级分类']=='医药保健销售店')
    df.loc[rule_4_6, ['小类']] = ['药店']
    
    rule_4_7 = (df['二级分类']=='动物医疗场所')
    df.loc[rule_4_7, ['小类']] = ['动物医院']
    
    rule_4_8 = (df['一级分类']=='医疗保健服务') & (df['小类'].isnull())
    df.loc[rule_4_8, ['小类']] = ['诊所']

    rule_5_1 = (df['二级分类']=='产业园区')
    df.loc[rule_5_1, ['大类','中类']] = ['工业功能','工业生产功能']
    
    rule_5_1_1 = (df['大类']=='工业功能') & (df['name'].str.contains('工业'))
    df.loc[rule_5_1_1, ['小类']] = ['工业园区']
    
    rule_5_1_2 = (df['大类']=='工业功能') & (df['name'].str.contains('工业')==False)
    df.loc[rule_5_1_2, ['小类']] = ['科技园区']
    
    rule_5_2 = (df['二级分类']=='住宅区')
    df.loc[rule_5_2, ['大类','中类','小类']] = ['居住功能','居住生活功能','住宅小区']
    
    rule_5_3 = (df['二级分类']=='楼宇')
    df.loc[rule_5_3, ['大类','中类','小类']] = ['工业功能','工业生产功能','商务金融']
    
    rule_5_4_1 = (df['二级分类']=='商务住宅相关') & (df['name'].str.contains('基地|软件园|产业园|科技园|电商园|物流园|创业园|创意园|智慧园|生态园|园区'))
    df.loc[rule_5_4_1, ['大类','中类','小类']] = ['工业功能','工业生产功能','科技园区']
    
    rule_5_4_2 = (df['二级分类']=='商务住宅相关') & (df['name'].str.contains('工业'))
    df.loc[rule_5_4_2, ['大类','中类','小类']] = ['工业功能','工业生产功能','工业园区']
    
    rule_5_4_3 = (df['二级分类']=='商务住宅相关') & ((df['name'].str.contains('佳兆业|万科|寓|轩|府|居|阁|苑|庭|村|期|里|湾|小区|公馆|别墅|住宅|宿舍|社区|栋|单元')) | (df['name'].str.contains('园') & df['小类'].isnull()))
    df.loc[rule_5_4_3, ['大类','中类','小类']] = ['居住功能','居住生活功能','住宅小区']
    
    rule_5_4_4 = (df['二级分类']=='商务住宅相关') & (df['name'].str.contains('商务|商业|大厦|国际'))
    df.loc[rule_5_4_4, ['大类','中类','小类']] = ['工业功能','工业生产功能','商务金融']

    rule_6 = (df['一级分类']=='政府机构及社会团体')
    df.loc[rule_6, ['大类','中类']] = ['公共管理服务功能','行政管理功能']
    
    rule_6_1 = (df['二级分类']=='政府机关')
    df.loc[rule_6_1, ['小类']] = ['政府机关']
    
    rule_6_2_1 = (df['二级分类']=='公检法机构') & (df['三级分类'].str.contains('公安警察|社会治安机构'))
    df.loc[rule_6_2_1, ['小类']] = ['公安机关']
    
    rule_6_2_2 = (df['二级分类']=='公检法机构') & (df['三级分类']=='检察院')
    df.loc[rule_6_2_2, ['小类']] = ['检察院']
    
    rule_6_2_3 = (df['二级分类']=='公检法机构') & (df['三级分类']=='法院')
    df.loc[rule_6_2_3, ['小类']] = ['法院']
    
    rule_6_2_4_1 = (df['二级分类']=='公检法机构') & (df['三级分类']=='公检法机关') & (df['name'].str.contains('公安|治安|派出所|警'))
    df.loc[rule_6_2_4_1, ['小类']] = ['公安机关']
    
    rule_6_2_4_2 = (df['二级分类']=='公检法机构') & (df['三级分类']=='公检法机关') & (df['name'].str.contains('检察'))
    df.loc[rule_6_2_4_2, ['小类']] = ['检察院']
    
    rule_6_2_4_3 = (df['二级分类']=='公检法机构') & (df['三级分类']=='公检法机关') & (df['name'].str.contains('司法|法院|法庭|仲裁'))
    df.loc[rule_6_2_4_3, ['小类']] = ['法院']
    
    rule_6_3 = (df['二级分类']=='工商税务机构')
    df.loc[rule_6_3, ['小类']] = ['工商税务机构']
    
    rule_6_4_1 = (df['二级分类']=='政府及社会团体相关') & (df['name'].str.contains('居委会|社区工作站|党群服务中心|街道办事处|委员会|办公室|局'))
    df.loc[rule_6_4_1, ['小类']] = ['政府机关']
    
    rule_6_4_2 = (df['二级分类']=='政府及社会团体相关') & (df['name'].str.contains('公安|治安|派出所|警'))
    df.loc[rule_6_4_2, ['小类']] = ['公安机关']
    
    rule_6_4_3 = (df['二级分类']=='政府及社会团体相关') & (df['name'].str.contains('检察'))
    df.loc[rule_6_4_3, ['小类']] = ['检察院']
    
    rule_6_4_4 = (df['二级分类']=='政府及社会团体相关') & (df['name'].str.contains('司法|法院|法庭|仲裁'))
    df.loc[rule_6_4_4, ['小类']] = ['法院']
    
    rule_6_4_5 = (df['二级分类']=='政府及社会团体相关') & (df['name'].str.contains('工商|税'))
    df.loc[rule_6_4_5, ['小类']] = ['工商税务机构']
    
    rule_7 = (df['一级分类']=='生活服务')
    df.loc[rule_7, ['大类','中类']] = ['商业服务业功能','生活服务功能']
    
    rule_7_1 = (df['二级分类'].str.contains('邮局|物流速递'))
    df.loc[rule_7_1, ['小类']] = ['物流服务']
    
    rule_7_2 = (df['二级分类'].str.contains('搬家公司|维修站点|洗衣店'))
    df.loc[rule_7_2, ['小类']] = ['家政维修']

    rule_7_3 = (df['二级分类'].str.contains('彩票彩券销售点|旅行社|美容美发店|摄影冲印店|洗浴推拿场所|婴儿服务场所'))
    df.loc[rule_7_3, ['小类']] = ['其他生活服务']
    
    rule_7_4_1 = (df['二级分类']=='生活服务场所') & (df['name'].str.contains('邮|菜鸟驿站|丰巢|自提|栈|柜|代理点|代办点|代收点'))
    df.loc[rule_7_4_1, ['小类']] = ['物流服务']
    
    rule_7_4_2 = (df['二级分类']=='生活服务场所') & (df['name'].str.contains('搬|修|洗衣|干洗')) & (df['name'].str.contains('装修')==False)
    df.loc[rule_7_4_2, ['小类']] = ['家政维修']
    
    rule_7_4_3 = (df['二级分类']=='生活服务场所') & (df['name'].str.contains('彩票|旅|美容|美发|美甲|烫染|造型|沙龙|养生|理发|摄影|照相|相馆|冲印|影楼|图文|水会|浴|足|会所|按摩|推拿|桑拿|SPA|婴|亲子'))
    df.loc[rule_7_4_3, ['小类']] = ['其他生活服务']
    
    rule_8 = (df['一级分类']=='科教文化服务')
    df.loc[rule_8, ['大类','中类']] = ['公共管理服务功能','文化教育功能']
    
    rule_8_1 = (df['二级分类']=='传媒机构')
    df.loc[rule_8_1, ['小类']] = ['新闻媒体']
    
    rule_8_2 = (df['二级分类']=='博物馆')
    df.loc[rule_8_2, ['小类']] = ['博物馆']
    
    rule_8_3 = (df['二级分类']=='图书馆')
    df.loc[rule_8_3, ['小类']] = ['图书馆']
    
    rule_8_4 = (df['二级分类']=='学校')
    df.loc[rule_8_4, ['小类']] = ['学校']
    
    rule_8_5 = (df['二级分类']=='培训机构')
    df.loc[rule_8_5, ['小类']] = ['培训机构']
    
    rule_8_6 = (df['二级分类']=='科研机构')
    df.loc[rule_8_6, ['小类']] = ['科研机构']
    
    rule_8_7_1 = (df['二级分类']=='科教文化场所') & ((df['name'].str.contains('传媒|传播|媒体|报社|电影|影视|影业|广播|电视|卫视|新闻|报业|记者|发行|杂志社|编辑'))|(df['name'].str.endswith('报')))
    df.loc[rule_8_7_1, ['小类']] = ['新闻媒体']
    
    rule_8_7_2 = (df['二级分类']=='科教文化场所') & (df['name'].str.contains('博物馆'))
    df.loc[rule_8_7_2, ['小类']] = ['博物馆']
    
    rule_8_7_3 = (df['二级分类']=='科教文化场所') & (df['name'].str.contains('图书馆'))
    df.loc[rule_8_7_3, ['小类']] = ['图书馆']
    
    rule_8_7_4 = (df['二级分类']=='科教文化场所') & ((df['name'].str.contains('学校|学院|体校|分校|教育集团|党校|幼儿园|幼稚园|小学|中学|大学'))|(df['name'].str.endswith('中|小|幼')))
    df.loc[rule_8_7_4, ['小类']] = ['学校']
    
    rule_8_7_5 = (df['二级分类']=='科教文化场所') & ((df['name'].str.contains('培|训|辅导|进修|考研|驾|琴|棋|书法|画|美术|舞蹈|作文|英语'))|((df['小类'].isnull()) & (df['name'].str.contains('教育'))))
    df.loc[rule_8_7_5, ['小类']] = ['学校']
    
    rule_8_7_6 = (df['二级分类']=='科教文化场所') & (df['name'].str.contains('实验|研究|设计院|研发'))
    df.loc[rule_8_7_6, ['小类']] = ['科研机构']

    rule_9 = (df['一级分类']=='购物服务')
    df.loc[rule_9, ['大类','中类']] = ['商业服务业功能','购物服务功能']
    
    rule_9_1 = (df['二级分类'].str.contains('商场|特色商业街'))
    df.loc[rule_9_1, ['小类']] = ['商场']
    
    rule_9_2 = (df['二级分类']=='超级市场')
    df.loc[rule_9_2, ['小类']] = ['超市']
    
    rule_9_3 = (df['二级分类']=='综合市场')
    df.loc[rule_9_3, ['小类']] = ['综合市场']
    
    rule_9_4 = (df['二级分类'].str.contains('家电电子卖场|家居建材市场|花鸟鱼虫市场'))
    df.loc[rule_9_4, ['小类']] = ['专业市场']

    rule_9_5 = (df['二级分类']=='便民商店/便利店')
    df.loc[rule_9_5, ['小类']] = ['便利店']
    
    rule_9_6_1 = (df['二级分类']=='购物相关场所') & (df['name'].str.contains('商场|商城|购物|广场|街')) & (df['name'].str.contains('(',regex=False)==False)
    df.loc[rule_9_6_1, ['小类']] = ['商场']
    
    rule_9_6_2 = (df['二级分类']=='购物相关场所') & (df['name'].str.contains('超市|屈臣氏|万宁|华润万|沃尔玛|山姆|家乐福|麦德龙|blt'))
    df.loc[rule_9_6_2, ['小类']] = ['超市']
    
    rule_9_6_3 = (df['二级分类']=='购物相关场所') & (df['name'].str.contains('果|菜|肉|禽|蛋|粮|油|水产|海鲜|批发|钱大妈')) & (df['小类'].isnull())
    df.loc[rule_9_6_3, ['小类']] = ['综合市场']
    
    rule_9_6_4 = (df['二级分类']=='购物相关场所') & (df['name'].str.contains('便利店|士多|美宜|全家|百里|天虹微喔|7-ELEVE|商店|商行|小卖部'))
    df.loc[rule_9_6_4, ['小类']] = ['便利店']
    
    rule_9_6_5 = (df['二级分类']=='购物相关场所') & (df['name'].str.contains('电|手机|通讯|通信|授权|音响|数码|空调|体验店|五金|照明|墙纸|瓷|寝|建材|装饰|卫浴|门窗|家具|玻璃|花|水族'))
    df.loc[rule_9_6_5, ['小类']] = ['专业市场']
    
    rule_9_7 = (df['二级分类'].str.contains('服装鞋帽皮具店|个人用品/化妆品店|体育用品店|文化用品店|专卖店'))
    df.loc[rule_9_7, ['小类']] = ['专卖店']
    
    rule_10_1 = (df['二级分类'].str.contains('银行'))
    df.loc[rule_10_1, ['大类','中类','小类']] = ['商业服务业功能','生活服务功能','金融服务']
    
    rule_10_2 = (df['二级分类'].str.contains('证券公司|保险公司'))
    df.loc[rule_10_2, ['大类','中类','小类']] = ['工业功能','工业生产功能','商务金融']

    rule_10_4_1 = (df['二级分类']=='金融保险服务机构') & (df['name'].str.contains('银行'))
    df.loc[rule_10_4_1, ['大类','中类','小类']] = ['商业服务业功能','生活服务功能','金融服务']
    
    rule_10_4_2 = (df['二级分类']=='金融保险服务机构') & (df['name'].str.contains('证券|险|人寿'))
    df.loc[rule_10_4_2, ['大类','中类','小类']] = ['工业功能','工业生产功能','商务金融']

    rule_11 = (df['一级分类']=='餐饮服务')
    df.loc[rule_11, ['大类','中类']] = ['商业服务业功能','餐饮服务功能']
    
    rule_11_1 = (df['二级分类']=='中餐厅')
    df.loc[rule_11_1, ['小类']] = ['中餐厅']
    
    rule_11_2 = (df['二级分类']=='外国餐厅')
    df.loc[rule_11_2, ['小类']] = ['外国餐厅']
    
    rule_11_3 = (df['二级分类']=='快餐厅')
    df.loc[rule_11_3, ['小类']] = ['快餐店']
    
    rule_11_4 = (df['二级分类'].str.contains('咖啡厅|冷饮店|甜品店|糕饼店|茶艺馆'))
    df.loc[rule_11_4, ['小类']] = ['饮料甜品店']

    rule_12 = (df['一级分类']=='风景名胜') & (df['二级分类']=='公园广场')
    df.loc[rule_12, ['大类','中类','小类']] = ['公共管理服务功能','游憩功能','公园']
    
    df = df[df['小类'].notnull()]
    return df

def poi_intersect(df, dfy):
    '''
    Goals: 重分类后的POI数据与分析范围相交
    Args: 
        df[dataframe]: POI数据
        dfy[geodataframe]: 分析范围
        netfish[geodataframe]: 渔网范围
    Returns:
        dfo[geodataframe]: 分析范围内的POI数据
    '''
    dfy = dfy.dissolve()
    POI = gpd.GeoDataFrame(df, geometry = gpd.points_from_xy(df['wgslng'], df['wgslat']))
    POI.crs = 'EPSG:4326' #按WGS84读取
    POI = POI.to_crs(epsg=4547) #转投影坐标
    dfo = POI[POI.within(dfy.geometry[0])]
    return dfo

def calc_index(dfo):
    '''
    Goals: 计算每个网格的相关指数
    Args: 
        dfo[geodataframe]: 分析范围内的POI栅格数据
    Returns:
        result[dataframe]: 各网格的指数结果表
    '''
    #计算功能密度
    density = dfo.groupby('index').aggregate({'id': 'count'}).reset_index()
    density['De'] = density['id']/0.25
    density['De*'] = density['De']/max(density['De'])
    
    #计算功能多样性
    diversity = pd.DataFrame(columns = ['index','m','Di'])
    for index in dfo['index'].unique():
        dfi = dfo[dfo['index']==index]
        summation = 0
        for item in dfi['小类'].unique():
            dfj = dfi[dfi['小类']==item]
            p = len(dfj)/len(dfi)
            product = p*ln(p)
            summation = summation + product
        Di = -1*(summation/ln(len(dfo['小类'].unique())))    
        diversity = diversity.append({'index': index, 'm': len(dfi['小类'].unique()), 'Di': Di}, ignore_index=True)
        
    case_1 = diversity['m'] != 1
    diversity.loc[case_1, ['Di*']] = diversity['Di']/ln(diversity['m'])
    case_2 = diversity['m'] == 1
    nonzero = diversity[diversity['Di']>0]
    diversity.loc[case_2, ['Di*']] = min(nonzero['Di'])/2
    
    #合并结果计算中心性指数
    result = pd.merge(density, diversity, on='index', how='inner')
    result['CI'] = result['De*']*result['Di*']
    return result

def explore_center(df_result, geo_relation, p_value, threshold):
    '''
    Goals: 根据指数结果识别中心范围，再根据面积及POI数量确定中心等级
    Args: 
        df_result[dataframe]: 含geometry的各网格指数结果表
        geo_relation[str]: 空间关系: [Queen, Rook]
    Returns:
        center_result[dataframe]: 含各中心面积、POI数量、等级的结果表
        polygons[geodataframe]: 包含每个独立的中心范围
    '''
    #生成权重计算显著度 Rook/Queen
    if geo_relation == 'Queen':
        w = libpysal.weights.Queen.from_dataframe(df_result)
    elif geo_relation == 'Rook':
        w = libpysal.weights.Rook.from_dataframe(df_result)
    lg = G_Local(df_result['CI'],w,transform='B')
    df_result['Z'] = lg.Zs
    df_result['P'] = lg.p_norm/2
    
    #根据显著度初步识别中心边界
    area_type = (df_result['Z'] > 0) & (df_result['P'] < p_value)
    df_result.loc[area_type, ['area_type']] = ['中心区']
    df_result = gpd.GeoDataFrame(df_result, geometry=df_result['geometry'])
    df_result.crs = 'EPSG:4547'
    
    #筛选中心网格，合并后炸开，使其成为连片独立的polygon
    center = df_result[df_result['area_type']=='中心区']
    center['geometry'] = center['geometry'].unary_union
    all_in_one = center.drop_duplicates(subset=['geometry'], keep='first')
    polygons = all_in_one.explode()
    
    #针对每一个polygon筛选出在其中的网格并计算面积和POI数量
    i = 0
    center_result = pd.DataFrame(columns = ['center_id','geometry','area','num_poi'])
    for polygon in polygons['geometry']:
        temp_center = df_result[df_result.within(polygon)]
        center_result = center_result.append({'center_id': i, 'geometry': polygon, 'area': polygon.area, 'num_poi': sum(temp_center['id'])}, ignore_index=True)
        i += 1

    #去除噪音
    center_result = center_result[center_result['num_poi'] > threshold*sum(df_result['id'])]
    
    #计算中心等级
    max_area = max(center_result['area'])
    max_poi = max(center_result['num_poi'])
    center_result['CL'] = (center_result['area']/max_area)*0.5 + (center_result['num_poi']/max_poi)*0.5
    
    level_1 = (center_result['CL'] == 1)
    center_result.loc[level_1, ['level']] = '主中心'
    
    level_2 = (center_result['area'] > 2000000) & (center_result['level'].isnull())
    center_result.loc[level_2, ['level']] = '次中心'
    
    level_3 = (center_result['level'].isnull())
    center_result.loc[level_3, ['level']] = '组团'
    
    return center_result, polygons

def func_decider(dfo, center_result, polygons, threshold):
    '''
    Goals: 计算各类功能的区位熵以确定中心功能
    Args: 
        dfo[geodataframe]: 分析范围内的POI栅格数据
        center_result[dataframe]: 含各中心面积、POI数量、等级的结果表
        polygons[geodataframe]: 包含每个独立的中心范围
        threshold[float]: 判断为综合功能的区位熵临界点
    Returns:
        final_result[geodataframe]: 最终结果表，包含各中心面积、POI数量、中心等级、中心功能、最大区位熵等
        entropy[dataframe]: 包含各中心各中类功能的区位熵结果表   
    '''
    j = 0
    entropy = pd.DataFrame(columns = ['center_id','geometry','function','LQ'])
    for polygon in polygons['geometry']:
        local_poi = dfo[dfo.within(polygon)]
        for item in local_poi['中类'].unique():
            local_items = local_poi[local_poi['中类']==item]
            global_items = dfo[dfo['中类']==item]
            if len(global_items) != 0:
                LQ = (len(local_items)/len(local_poi))/(len(global_items)/len(dfo))
            if item == '居住生活功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '居住生活功能', 'LQ': LQ}, ignore_index=True)
            elif item == '工业生产功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '工业生产功能', 'LQ': LQ}, ignore_index=True)
            elif item == '餐饮服务功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '餐饮服务功能', 'LQ': LQ}, ignore_index=True)
            elif item == '购物服务功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '购物服务功能', 'LQ': LQ}, ignore_index=True)
            elif item == '生活服务功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '生活服务功能', 'LQ': LQ}, ignore_index=True)
            elif item == '住宿服务功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '住宿服务功能', 'LQ': LQ}, ignore_index=True)
            elif item == '休闲娱乐功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '休闲娱乐功能', 'LQ': LQ}, ignore_index=True)
            elif item == '行政管理功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '行政管理功能', 'LQ': LQ}, ignore_index=True)
            elif item == '医疗健康功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '医疗健康功能', 'LQ': LQ}, ignore_index=True)
            elif item == '文化教育功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '文化教育功能', 'LQ': LQ}, ignore_index=True)
            elif item == '游憩功能':
                entropy = entropy.append({'center_id': j, 'geometry': polygon, 'function': '游憩功能', 'LQ': LQ}, ignore_index=True)
        j += 1
    entropy_result = entropy.iloc[entropy.groupby('center_id')['LQ'].agg(pd.Series.idxmax)]
    decision = (entropy_result['LQ'] <= threshold)
    entropy_result.loc[decision, ['function']] = '综合功能'
    #合并
    final_result = pd.merge(center_result, entropy_result[['center_id','function','LQ']], on='center_id', how='inner')
    final_result = gpd.GeoDataFrame(final_result, geometry=final_result['geometry'])
    final_result.crs = 'EPSG:4547'
    return final_result, entropy

if __name__ == "__main__":
    main()
