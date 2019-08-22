var g_same = false;
var rt_t1=null;
var has_set_interval=false;
var disk_vol_info = null;
var master_dns_index = 0;
var standby_dns_index = 0;
var ip_not_switch_dns_index = 0;
var g_op_type='create_plan';
var g_have_check_dest_host_in_plan = false;
var latest_point = '';
var g_restore_time = '';
var disk_checker = new DiskCheck();

$('input[name=timetype]').click(function () {
    if (g_op_type == 'create_plan'){
        var id=$(this).attr('id');
        if( id == 'push_new'){
            $('#stable_point_container').hide();
        }else {
            $('#stable_point_container').show();
        }
    }else {
        $('#stable_point_container').hide();
    }
})

function _isTime(input)
{
	//判断是不是日期和时间的：yyyy-MM-dd HH:CC:SS.000000
	var patrn= /^([1][7-9][0-9][0-9]|[2][0][0-9][0-9])(\-)([0][1-9]|[1][0-2])(\-)([0][1-9]|[1-2][0-9]|[3][0-1])(.)([0-1][0-9]|[2][0-3])(:)([0-5][0-9])(:)([0-5][0-9])(\.)(\d{1,6})$/g;
	if( patrn.exec(input) )
	{
		return true;
	}
	else
	{
		return false;
	}
}

function typeFmatter(cellvalue, options, rowObjec)
{
	var type = cellvalue;
	if(type=='cdp')
	{
		return 'CDP';
	}
	if (type=='normal')
	{
		return '备份点';
	}
	return cellvalue;
}

jQuery("#point_list").jqGrid({
	datatype: "local",
	height: 200,
	width: 700,
   	colNames:['id','point_id', '备份点', '类型', 'recommend'],
   	colModel:[
   		{name:'id',index:'id', width:60,hidden:true},
        {name:'point_id',index:'point_id', width:60,hidden:true},
   		{name:'time',index:'time', width:150, sorttype:"date"},
   		{name:'type',index:'type', width:50,align:"center",formatter:typeFmatter},
   		{name:'recommend',index:'recommend', hidden:true, width:30, align:"center"}
   	],
   	multiselect: true,
    pager:'#point_list_page',
    rowNum:10,
    rowList:[10,20,30],
    recordpos: 'left',
    viewrecords: true,
   	caption: "请选择需要同步到备份点",
	onSelectRow: function(ids) {
		var type=$('#point_list').jqGrid('getRowData',ids).type;
		var id=$('#point_list').jqGrid('getRowData',ids).point_id;
		console.log('choice id:'+ id);
		$('#pointid').val(id);
		if( type == 'CDP' )
		{
			var vec = id.split('|');
			var cdpbackupstarttime = vec[2].replace(/T/g, ' ');
			var cdpbackupendtime = vec[3].replace(/T/g, ' ');
			$('#show_cdpbackupstarttime').html(cdpbackupstarttime);
			$('#show_cdpbackupendtime').html(cdpbackupendtime);
			$('#restoretime').val(cdpbackupendtime);
			getRestoreTimeInit(id);
			$('#cdp_ui_id').show();
			$('#tabs').scrollTop(500);
		}
		else
		{
			$('#cdp_ui_id').hide();
		}
	},
	beforeSelectRow: function(rowid, e)
	{
		$(this).jqGrid('resetSelection');
		return(true);
	},
	gridComplete:function(){
		$('#cb_point_list').hide();
	}
});

var s_d_vue = new Vue({
    'el':'#s_d_vue',
    data:{
        moment_obj : '',
        time: '',
        today: moment().format('YYYY-MM-DD'),
        error:'',
        host_ident:''
    },
    computed:{
        disabled:function () {
            return moment(this.time) >= moment(this.today)
        }
    },
    methods:{
        to_next:function () {
            this.moment_obj.add(1, 'd');
            this.update_time();
            this.get_new_point();
            $('#pointid').val('');
        },
        to_pre:function () {
            this.moment_obj.add(-1, 'd');
            this.update_time();
            this.get_new_point();
            $('#pointid').val('');
        },
        update_time:function () {
            this.time = this.moment_obj.format('YYYY-MM-DD')
        },
        get_new_point:function () {
            $('#cdp_ui_id').hide();
            this.error = '';
            jQuery("#point_list").jqGrid("clearGridData");
            myAjaxGet('../hotbackup_handle/', 'a=get_point_list&host_ident='+this.host_ident+'&st_date='+this.time,
                hotbackupSwitch_call_back);
        },
        init:function () {
            this.error = '';
        }
    }
})

function hotbackupSwitch_call_back(jsonobj) {
	if (jsonobj.r != 0)
	{
        openErrorDialog('错误',jsonobj.e);
		return;
    }

	if (jsonobj.list.length==0)
	{
		s_d_vue.error = s_d_vue.time + '没有备份点，重新选择其它时间';
		return;
	}
	s_d_vue.time = jsonobj.st_date;
	s_d_vue.moment_obj = moment(jsonobj.st_date);
	jQuery("#point_list").jqGrid("clearGridData");

	for(var i=0;i<jsonobj.list.length;i++)
	{
		var id = jsonobj.list[i].id;
		var time = jsonobj.list[i].time;
		var type = jsonobj.list[i].type;
		var recommend = jsonobj.list[i].recommend;
		if(recommend==false)
		{
			recommend='否';
		}
		else
		{
			recommend='是';
		}
		var tmp = {id:id,point_id:id,time:time,type:type,recommend:recommend};
		jQuery("#point_list").jqGrid('addRowData',i+1,tmp);

	}

	jQuery("#point_list").trigger("reloadGrid");
}

function getRestoreTimeInit(pointid) {
    // 浏览器是否支持vis.js
    $.getScript("/static/js/vis.js")
    .done(function() {
        $('#yDscr').hide();
        $('#visualization').empty();
        $('#visualization2').empty();
        var parms = pointid.split('|');
        var minTime = parms[2];
        var maxTime = parms[3];
        var container = $('#visualization')[0];
        var items = new vis.DataSet([]);

        var options = {
            showCurrentTime: false,
            min: minTime,
            max: maxTime,
            start: minTime,
            end: maxTime,
            format: {
                minorLabels: {
                    millisecond: 'SSSSSS',
                    second: 'ss',
                    minute: 'HH:mm',
                    hour: 'HH:mm',
                    weekday: 'DD[日]',
                    day: 'DD[日]',
                    month: 'MM[月]',
                    year: 'YYYY[年]'
                },
                majorLabels: {
                    millisecond: 'YYYY[年]MM[月]DD[日] HH:mm:ss',
                    second: 'YYYY[年]MM[月]DD[日] HH:mm',
                    minute: 'YYYY[年]MM[月]DD[日]',
                    hour: 'YYYY[年]MM[月]DD[日]',
                    weekday: 'YYYY[年]MM[月]',
                    day: 'YYYY[年]MM[月]',
                    month: 'YYYY[年]',
                    year: ''
                }
            },
            height: '45px'
        };
        timelineObj = new vis.Timeline(container, items, options);
        var inputTime = $('#restoretime').val();
        timelineObj.addCustomTime(inputTime, 'timebar');  // 初始以“输入时间”作默认位置

        toGetTimesScores();
    })
    .fail(function() {
        $('#select_cdp_point').empty();
        $('#select_cdp_point').append('请更换高版本浏览器');
        $('#select_cdp_point').show();
    });
}

// 选择大致时间：1.作为窗口轴值  2.作为还原点时间
$('#visualization').click(function (event) {
    var props = timelineObj.getEventProperties(event);
    timelineObj.removeCustomTime('timebar');
    timelineObj.addCustomTime(props.time, 'timebar');
    var centreTimeStp = props.time.getTime();

    var _Date = new Date(centreTimeStp);
    var time = _Date.Format("yyyy-MM-dd hh:mm:ss.S");
    $('#restoretime').val(time);
    $('#restoretime').fadeOut(80).fadeIn(80);
    $('#restoretime').fadeOut(80).fadeIn(80);
    $('#restoretime').fadeOut(80).fadeIn(80);
    toGetTimesScores()
});

// 点击曲线图，作为还原点时间
$('#visualization2').click(function (event){
    if(window.waiting){
        return;
    }
    var props = graph2d.getEventProperties(event);
    var timeSelectedStp = props.time.getTime();

    try{
        graph2d.addCustomTime(props.time, 'timebar2');
    }
    // 存在bar，异常的处理
    catch(err) {
        graph2d.removeCustomTime('timebar2');
        graph2d.addCustomTime(props.time, 'timebar2');
    }

    var _Date = new Date(timeSelectedStp);
    var time = _Date.Format("yyyy-MM-dd hh:mm:ss.S");
    $('#restoretime').val(time);
    $('#restoretime').fadeOut(80).fadeIn(80);
    $('#restoretime').fadeOut(80).fadeIn(80);
    $('#restoretime').fadeOut(80).fadeIn(80);
});

function check_input_time(time) {
    if(!_isTime(time)){
        openErrorDialog({title:'错误',html:"请填写正确的时间格式：YYYY-MM-DD hh:ss:ii.dddddd"});
        return false;
    }
    return true;
}

function holdWhenCdpUI(hold) {
    if(hold){
        $('#Msg4CdpUI').text('读取数据中...');
        $('#IsShowCdpUI').show();
    }
    else {
        $('#IsShowCdpUI').hide();
    }
}

// 触发：初始，切换窗口，选择窗口轴
function toGetTimesScores() {
    var _pointParms = $('#pointid').val().split('|');
    var _windowSize = $("input[name='windowSize']:checked").val();
    var _centreTime = $('#restoretime').val();  // 始终取还原时间作为：窗口轴
    if(!check_input_time(_centreTime)){
            return;
    }
    var _centreTimeStp = moment(_centreTime).toDate().getTime();

    var params = 'a=getiodaychart&centre={0}&window={1}&id={2}&sliceend={3}'
        .replace('{0}', _centreTimeStp).replace('{1}', _windowSize).replace('{2}', _pointParms[1]).replace('{3}', _pointParms[3]);
    holdWhenCdpUI(true);
    window.waiting = true;
    myAjaxGet('../restore_handle/', params, drawScore);
}

$("input[name='windowSize']").change(toGetTimesScores);

function is_input_time_out_range(time) {
    var cdpStarTime = $('#pointid').val().split('|')[2];
    var cdpEndTime = $('#pointid').val().split('|')[3];
    cdpStarTime = moment(cdpStarTime).toDate().getTime();
    cdpEndTime = moment(cdpEndTime).toDate().getTime();
    var inputTime = moment(time).toDate().getTime();
    if(inputTime < cdpStarTime || inputTime > cdpEndTime){
        openErrorDialog({title:'错误',html:"输入时间不在范围内"});
        return true;
    }
    return false;
}

// 监听“还原时间点”输入控件：回车事件
$('#restoretime').keypress(function (event) {
    if(event.key == "Enter"){
        // 设置窗口轴值
        var inputTime = $('#restoretime').val();
        if(!check_input_time(inputTime)){           // 格式
            return;
        }
        if(is_input_time_out_range(inputTime)){     // 范围
            return;
        }
        timelineObj.removeCustomTime('timebar');
        timelineObj.addCustomTime(inputTime, 'timebar');
        getRestoreTimeInit($('#pointid').val());
    }
});

function drawScore(jstr) {
    window.waiting = false;
    holdWhenCdpUI(false);
    $('#yDscr').show();
    $('#visualization2').empty();
    var _container = $('#visualization2')[0];
    var _dataset = new vis.DataSet([]);

    // jstr.times_scores长度始终：>=1
    // 向其尾后追加一元素
    var lastElem = jstr.times_scores[jstr.times_scores.length - 1];
    var lastTime = new Date(moment(lastElem.time).toDate().getTime() + 100).Format("yyyy-MM-dd hh:mm:ss.S");
    var lastScore = lastElem.score;
    jstr.times_scores.push({'time':lastTime, 'score': lastScore});

    // 首尾添加最值：固定高度
    var Elem0 = jstr.times_scores[0];
    var Elemn = jstr.times_scores[jstr.times_scores.length - 1];
    var starT = new Date(moment(Elem0.time).toDate().getTime() - 1).Format("yyyy-MM-dd hh:mm:ss.S");
    var endT = new Date(moment(Elemn.time).toDate().getTime() + 1).Format("yyyy-MM-dd hh:mm:ss.S");
    jstr.times_scores.unshift({'time':starT, 'score': -1});
    jstr.times_scores.push({'time':endT, 'score': -1});

    var max_secs = 1;
    var vtime = null;
    var vsecs = null;
    $.each(jstr.times_scores, function (i, time_score) {
        vtime = time_score.time;
        vsecs = time_score.score;
        if(vsecs > max_secs || vsecs == -1){
            vsecs = max_secs;
        }
        _dataset.add({x: vtime, y: vsecs * 1000, group: 0});
    });

    var groupData = {
        id: 0,
        options: {
            drawPoints: {
                style: 'circle',
                size: 3
            },
            shaded: {
                orientation: 'bottom'
            }
        }
    };
    var _groups = new vis.DataSet([groupData]);
    var minTime = jstr.times_scores[0].time;
    var maxTime = jstr.times_scores.pop().time;

    var pointParams = $('#pointid').val().split('|');
    var pointEndTime = moment(pointParams[3].replace('T', ' ')).toDate();
    var _maxTime = moment(maxTime).toDate();
    if (_maxTime > pointEndTime){
        maxTime = pointEndTime;
    }

    var _options = {
        showCurrentTime: false,
        interpolation: false,
        start: minTime,
        min: minTime,
        end: maxTime,
        max: maxTime,
        dataAxis: {
            visible: false,
            icons: false,
            left: {
                range: {
                    min: 0,
                    max: 1000 + 50
                }
            }
        },
        height: '270px'
    };
    graph2d = new vis.Graph2d(_container, _dataset, _groups, _options);

    graph2d.addCustomTime($('#restoretime').val(), 'timebar2');
    $('#select_cdp_point').show();
}

function get_point_id() {
    if (g_op_type == 'create_plan'){
        var timetype=$("input[name='timetype']:checked").val();
        if (timetype == '1'){
            var point_id = latest_point; // 持续推送到最新的时间点
        }else {
            var row = get_select_row('point_list'); //推送到固定时间点
            if (!row){
                return ''
            }else{
                return row.point_id;
            }
        }
        return point_id;
    }else {
        return $('#pointid').val();
    }
}

function get_restore_time() {
    if (g_op_type == 'create_plan'){
        // 推送到最新
        if($('input[name=timetype]:checked').val() == '1'){
            var time_str = get_point_id().split('|').pop();
        }else {
            var point_id = get_point_id(),
                item = point_id.split('|');
            if (item[0] == 'normal'){
                var time_str = item.pop();
            }else{
                var time_str = $('#restoretime').val();
            }
        }
        return time_str
    }else {
        return g_restore_time
    }
}

function CheckHotBackupIP(div_id)
{
    if (div_id == 'not_switch_ip_adptersettings_div'){
        var jsonobj = $.evalJSON(GetAdpterSettings_not_switch_ip('not_switch_ip_adptersettings_div'));
    }else{
        var jsonobj = $.evalJSON(GetAdpterSettings(div_id));
    }
	var pcname = '备服务器';
	if('master_adptersettings_div' == div_id )
	{
		pcname = '主服务器';
	}


	var control_ip = '',
	    gtw_reachable = false,
	    gate_way = jsonobj.gateway[0];

    // 可以不填写网关
    if (gate_way != ''){
        if (!isIPV4(gate_way)){
            openErrorDialog({title:'错误',html:pcname+"（全局）默认网关"+gate_way+"不正确"});
		    return false;
        }
    }else {
        gtw_reachable = true;
    }

    var have_control_ip = false;

	for(var i=0;i<jsonobj.control.length;i++)
	{
		for(var j=0;j<jsonobj.control[i].ips.length>0;j++)
		{
		    have_control_ip = true;
			control_ip = jsonobj.control[i].ips[j].ip;
			if(!isIPV4(control_ip))
			{
				openErrorDialog({title:'错误',html:pcname+"IP"+control_ip+"不正确"});
				return false;
			}
			if(!isMask(jsonobj.control[i].ips[j].mask))
			{
				openErrorDialog({title:'错误',html:pcname+"子网掩码"+jsonobj.control[i].ips[j].mask+"不正确"});
				return false;
			}
			if (gateway_reachable(jsonobj.control[i].ips[j].ip, jsonobj.control[i].ips[j].mask, gate_way)){
			    gtw_reachable = true;
            }
		}
	}

	// 禁用漂移IP 不需要校验
    if (div_id == 'not_switch_ip_adptersettings_div'){
        if (!have_control_ip){
            openErrorDialog({title:'错误',html:pcname+"IP或子网掩码不能为空或者请输入合法的IP地址"});
            return false;
        }
    }else{
        if(jsonobj.business.length==0)
        {
            openErrorDialog({title:'错误',html:pcname+"网卡信息错误"});
            return false;
        }

        if(jsonobj.business[0].length==0)
        {
            openErrorDialog({title:'错误',html:pcname+"网卡信息错误"});
            return false;
        }
    }

	var haveIP = false;

	for(var i=0;i<jsonobj.business.length;i++)
	{
		for(var j=0;j<jsonobj.business[i].ips.length>0;j++)
		{
			haveIP = true;
			if(!isIPV4(jsonobj.business[i].ips[j].ip))
			{
				openErrorDialog({title:'错误',html:pcname+"IP"+jsonobj.business[i].ips[j].ip+"不正确"});
				return false;
			}
			if(control_ip == jsonobj.business[i].ips[j].ip )
			{
				openErrorDialog({title:'错误',html:pcname+"2个IP地址相同  \n("+control_ip+")"});
				return false;
			}
			if(!isMask(jsonobj.business[i].ips[j].mask))
			{
				openErrorDialog({title:'错误',html:pcname+"子网掩码"+jsonobj.business[i].ips[j].mask+"不正确"});
				return false;
			}
			if (gateway_reachable(jsonobj.business[i].ips[j].ip, jsonobj.business[i].ips[j].mask, gate_way)){
			    gtw_reachable = true;
            }
		}
	}

	if( haveIP == false && div_id != 'not_switch_ip_adptersettings_div')
	{
		if ($('#remote_server_fieldset').is(':visible'))
		{
		}
		else if ($("input[name='switch_change_master_ip']:checked").val()==0)
		{
		}
		else
		{
			openErrorDialog({title:'错误',html:pcname+"漂移IP或子网掩码不能为空或者请输入合法的IP地址"});
			return false;
		}
	}

	if (!gtw_reachable){
	    var msg = '操作失败，' + pcname + '默认网关[ '+gate_way+' ]不在由IP地址和子网掩码定义的同一网络段（子网）上。';
	    openErrorDialog({title:'错误',html:msg});
		return false;
    }

	for(var i=0;i<jsonobj.route.length;i++)
	{
		if(!isIPV4(jsonobj.route[i].ip))
		{
			openErrorDialog({title:'错误',html:pcname+"漂移目标网络"+jsonobj.route[i].ip+"不正确"});
			return false;
		}

		if(!isMask(jsonobj.route[i].mask))
		{
			openErrorDialog({title:'错误',html:pcname+"（路由）子网掩码"+jsonobj.route[i].mask+"不正确"});
			return false;
		}

		if(!isIPV4(jsonobj.route[i].gateway))
		{
			openErrorDialog({title:'错误',html:pcname+"（路由）网关"+jsonobj.route[i].gateway+"不正确"});
			return false;
		}
	}

	/*
	可以不填写DNS
	if(jsonobj.dns.length==0)
	{
		openErrorDialog({title:'错误',html:pcname+"（全局）DNS不能为空"});
		return false;
	}
    */

	for(var i=0;i<jsonobj.dns.length;i++)
	{
		if(!isIPV4(jsonobj.dns[i]))
		{
			openErrorDialog({title:'错误',html:pcname+"DNS"+jsonobj.dns[i]+"不正确"});
			return false;
		}
	}

	return true;
}

function CheckData(selindex)
{
	if(selindex==0)
	{
		var serverid=getaciTreeChecked('Servers_Tree1');

		if(serverid=='')
		{
			openErrorDialog({title:'错误',html:'请选择热备源客户端'});
			return false;
		}
	}
	else if(selindex==1)
	{
		var serverid=getaciTreeChecked('Servers_Tree2');

		if(serverid=='')
		{
			openErrorDialog({title:'错误',html:'请选择热备目标'});
			return false;
		}

		if (getaciTreeChecked('Servers_Tree1')==getaciTreeChecked('Servers_Tree2'))
		{
			openErrorDialog({title:'错误',html:"源和目标客户端不能相同"});
			return false;
		}
	}
	else if(selindex==3)
	{
		var switchtype=$("input[name='"+getswitchtype_input_name()+"']:checked").val();
		if(switchtype == 2)
		{
			var bdetect = false;
			if($('div#'+get_ip_switch_tabs_div_id()+' #detect_arbitrate_2_master_business_ip').prop('checked'))
			{
				bdetect = true;
			}

			if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_control_ip').prop('checked'))
			{
				bdetect = true;
			}

			if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_business_ip').prop('checked'))
			{
				bdetect = true;
			}
			if(bdetect == false)
			{
				openErrorDialog({title:'错误',html:'请至少选择一条主备切换的条件'});
				return false;
			}
		}

		var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();

		if(switch_change_master_ip==0)
		{
			//禁用漂移IP
            if(CheckHotBackupIP('not_switch_ip_adptersettings_div') == false)
			{
				return false;
			}
		}
		else
		{
			if(CheckHotBackupIP('master_adptersettings_div') == false)
			{
				return false;
			}

			if(CheckHotBackupIP('standby_adptersettings_div') == false)
			{
				return false;
			}
		}


	}
	else if(selindex==10)
	{
		if($('#stop_script_exe_name').val()!='')
		{
			if($('#stop_script_zip').val() == '')
			{
				openErrorDialog({title:'错误',html:'可执行文件名不为空时，请选择上传文件'});
				return false;
			}
		}

		if($('#start_script_exe_name').val()!='')
		{
			if($('#start_script_zip').val() == '')
			{
				openErrorDialog({title:'错误',html:'可执行文件名不为空时，请选择上传文件'});
				return false;
			}
		}
	}
	return true;
}

function restoresuccess(jsonstr,immediately)
{
    $('.mywaite').hide();
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	$( "#tabs" ).tabs( "option", "active", 0 );
	$('#pointid').val('none');
	var bplanui=false;
	try
	{
		$("#newhotbackupFormDiv").dialog('close');
	}
	catch (e)
	{
		//新建热备计划页面
		bplanui = true;
		var html='新建热备计划成功，您可在<a href="../mgrhotbackup" style="color:blue;">热备计划管理</a>中查看已创建的计划任务。';
		if(immediately==1)
		{
			var html='新建热备计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况，或在<a href="../mgrhotbackup" style="color:blue;">热备计划管理</a>中管理已创建的热备计划。';
		}
		openSuccessDialog({title:'完成',html:html});
	}

	if(bplanui==false)
	{
		//热备计划管理页面
		if(immediately==1)
		{
			var html='新建热备计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况。';
			openSuccessDialog({title:'完成',html:html});
		}
	}

	try
	{
		setTimeout(function() {
	    	var new_url = '../hotbackup_handle/?a=listplan';
			$('#list').setGridParam({url:new_url});
			$('#list').trigger("reloadGrid",[{page:1}]);
	    }, 2000);
	}
	catch (e)
	{

	}
}

function GetAdpterSettings_not_switch_ip(adpter_settings_div)
{
	var adpter = {'control':[],'business':[],'route':[],'dns':[],'gateway':[]};
	gateway=$('#'+adpter_settings_div).find('input[name=gateway]').val();
	adpter['gateway'].push(gateway);

	var last_adpter_id = null;
	var last_mac = null;
	var business_ips = [];
	var obj = $('#'+adpter_settings_div).find('.class_adpter_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='adapter_id')
		{
			if(last_adpter_id!=null)
			{
				adpter['control'].push({'id':last_adpter_id,'mac':last_mac,'ips':business_ips});
				business_ips = [];
			}
			last_adpter_id=input_obj.value;
		}

		if(input_obj.name=='mac')
		{
			last_mac=input_obj.value.split('（')[0];
		}

		if(input_obj.name=='service_ip')
		{
			service_ip=input_obj.value;
		}

		if(input_obj.name=='route_mask')
		{
			route_mask=input_obj.value;
			if(isIPV4(service_ip) && isMask(route_mask))
			{
				business_ips.push({'ip':service_ip,'mask':route_mask});
			}
		}
	}

	if(last_adpter_id!=null)
	{
		adpter['control'].push({'id':last_adpter_id,'mac':last_mac,'ips':business_ips});
	}

	var route_ip = null;
	var route_mask = null;
	var route_gateway = null;
	var obj = $('#'+adpter_settings_div).find('.class_route_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='route_ip')
		{
			route_ip = input_obj.value;
		}
		if(input_obj.name=='route_mask')
		{
			route_mask = input_obj.value;
		}
		if(input_obj.name=='route_gate')
		{
			route_gateway = input_obj.value;
			if(!isEmpty(route_ip) || !isEmpty(route_mask) || !isEmpty(route_gateway))
			{
				adpter['route'].push({'ip':route_ip,'mask':route_mask,'gateway':route_gateway});
			}
		}
	}

	var obj = $('#'+adpter_settings_div).find('.class_dns_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='dns')
		{
			dns = input_obj.value;
			if(!isEmpty(dns))
			{
				adpter['dns'].push(dns);
			}
		}
	}

	return $.toJSON(adpter);
}


function GetAdpterSettings(adpter_settings_div)
{
	var adpter = {'control':[],'business':[],'route':[],'dns':[],'gateway':[]};
	adpter_id=$('#'+adpter_settings_div).find('select[name=control_adpter]').val();
	control_ip=$('#'+adpter_settings_div).find('input[name=control_ip]').val();
	control_mask=$('#'+adpter_settings_div).find('input[name=route_mask]').val();
	gateway=$('#'+adpter_settings_div).find('input[name=gateway]').val();
	var control_ips = [];
	control_ips.push({'ip':control_ip,'mask':control_mask});
	adpter['control'].push({'id':adpter_id.split('|')[0],'mac':adpter_id.split('|')[1].split('（')[0],'ips':control_ips});
	adpter['gateway'].push(gateway);

	var last_adpter_id = null;
	var last_mac = null;
	var business_ips = [];
	var obj = $('#'+adpter_settings_div).find('.class_adpter_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='adapter_id')
		{
			if(last_adpter_id!=null)
			{
				adpter['business'].push({'id':last_adpter_id,'mac':last_mac,'ips':business_ips});
				business_ips = [];
			}
			last_adpter_id=input_obj.value;
		}

		if(input_obj.name=='mac')
		{
			last_mac=input_obj.value.split('（')[0];
		}

		if(input_obj.name=='service_ip')
		{
			service_ip=input_obj.value;
		}

		if(input_obj.name=='route_mask')
		{
			route_mask=input_obj.value;
			if(isIPV4(service_ip) && isMask(route_mask))
			{
				business_ips.push({'ip':service_ip,'mask':route_mask});
			}
		}
	}

	if(last_adpter_id!=null)
	{
		adpter['business'].push({'id':last_adpter_id,'mac':last_mac,'ips':business_ips});
	}

	var route_ip = null;
	var route_mask = null;
	var route_gateway = null;
	var obj = $('#'+adpter_settings_div).find('.class_route_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='route_ip')
		{
			route_ip = input_obj.value;
		}
		if(input_obj.name=='route_mask')
		{
			route_mask = input_obj.value;
		}
		if(input_obj.name=='route_gate')
		{
			route_gateway = input_obj.value;
			if(!isEmpty(route_ip) || !isEmpty(route_mask) || !isEmpty(route_gateway))
			{
				adpter['route'].push({'ip':route_ip,'mask':route_mask,'gateway':route_gateway});
			}
		}
	}

	var obj = $('#'+adpter_settings_div).find('.class_dns_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='dns')
		{
			dns = input_obj.value;
			if(!isEmpty(dns))
			{
				adpter['dns'].push(dns);
			}
		}
	}

	return $.toJSON(adpter);
}

function isRepeat(arr)
{
	var hash = {};

	for(var i in arr)
	{
		if(hash[arr[i]])
			return true;
		hash[arr[i]] = true;
	}
	return false;
}

function CheckDisks(disks)
{
	var destdisk = new Array();
	for(var i=0;i<disks.length;i++)
	{
		destdisk.push(disks[i].dest);
	}

	if( isRepeat(destdisk) )
	{
		openErrorDialog({title:'错误',html:'多个源硬盘不能恢复到同一目标硬盘'});
		return false;
	}

	return true;
}

function get_restore_mod() {
    // 界面勾选， 代表使用快速还原技术
    return $('input[name=restore_model]').is(':checked') ? 0 : 1
}

function OnHotBackup(StartFilepath,StopFilepath)
{
	var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
	var standby_adpter = '{}';
	var master_adpter = '{}';
	if(switch_change_master_ip==1)
	{
		standby_adpter = GetAdpterSettings('standby_adptersettings_div');
		master_adpter = GetAdpterSettings('master_adptersettings_div');
	}
	else
	{
		standby_adpter = GetAdpterSettings_not_switch_ip('not_switch_ip_adptersettings_div');
	}
	var src_ident=getaciTreeChecked('Servers_Tree1');
	var dest_ident=getaciTreeChecked('Servers_Tree2');
	var tmpid=GetAciTreeParent('Servers_Tree2',dest_ident);
	var restoretype=$("input[name='restoretype']:checked").val();
	if(tmpid=='ui_1' && restoretype ==1) // 整机恢复
	{
		dest_ident=GetAciTreeValueRadio('Servers_Tree2',dest_ident,'peserverid');
	}
	var timetype=$("input[name='timetype']:checked").val();
	var switchtype=$("input[name='"+getswitchtype_input_name()+"']:checked").val();
	var switchback=$('#switchback').prop("checked") ? '1' : '0';
	var test_timeinterval = $('#test_timeinterval').val();
	var test_frequency = $('#test_frequency').val();
	var arbitrate_ip = $('div#'+get_ip_switch_tabs_div_id()+' input[name=arbitrate_ip]').val();

	var params = 'src_ident='+src_ident;
	params += '&dest_ident='+dest_ident;
	params += '&name='+$('#taskname').val();
	params += '&timetype='+timetype;
	params += '&restoretype='+restoretype;
	params += '&switchtype='+switchtype;
	params += '&test_timeinterval='+test_timeinterval;
	params += '&test_frequency='+test_frequency;
	params += '&arbitrate_ip='+arbitrate_ip;
	params += '&master_adpter='+master_adpter;
	params += '&standby_adpter='+standby_adpter;
	params += '&pointid='+get_point_id();
	params += '&point_time='+$('#point_time').html();
	params += '&restore_time='+get_restore_time();
	params += '&switch_change_master_ip='+switch_change_master_ip;

	if(switchtype==2)
	{
		var detect_arbitrate_2_master_business_ip = 0;
		var detect_aio_2_master_control_ip = 0;
		var detect_aio_2_master_business_ip = 0;
		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_arbitrate_2_master_business_ip').prop('checked'))
		{
			detect_arbitrate_2_master_business_ip = 1;
		}

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_control_ip').prop('checked'))
		{
			detect_aio_2_master_control_ip = 1;
		}

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_business_ip').prop('checked'))
		{
			detect_aio_2_master_business_ip = 1;
		}
		params += '&detect_arbitrate_2_master_business_ip='+detect_arbitrate_2_master_business_ip;
		params += '&detect_aio_2_master_control_ip='+detect_aio_2_master_control_ip;
		params += '&detect_aio_2_master_business_ip='+detect_aio_2_master_business_ip;
	}

	if( restoretype == 1 )
	{
		//整机恢复参数
		var drivers_ids = '';
		var drivers_type = '1';
		var drivers_ids_force = '';
		if($('input[name=choice_method]:checked').val() ==2){
			drivers_ids = getAciTreeBoxChecked('driver_version_tree');
			drivers_ids_force = getaciTreeChecked('driver_version_tree');
			drivers_type = '2';
		}
		params+="&drivers_ids="+encodeURIComponent(drivers_ids)+'&drivers_type='+drivers_type;
        params+="&drivers_ids_force="+encodeURIComponent(drivers_ids_force);

		var disk_info = verify_disks();
		if (!disk_info){
		    return;
        }
		params+="&disks="+JSON.stringify(disk_info.disks);
		params+="&ex_vols="+JSON.stringify(disk_info.ex_vols);
		params+="&boot_vols="+JSON.stringify(disk_info.boot_vols);
		params+="&disable_fast_boot="+get_restore_mod();
	}
	else
	{
		var lines = $('#disk_vol_div li');
		var choice_maps = [];
		$.each(lines, function (index, item) {
			if ($(item).find('input').is(':checked')) {
				var c_val = $(item).find('select').val();
				choice_maps.push(c_val);
			} else {
				choice_maps.push(null);
			}
		});
		params += '&vol_maps=' + JSON.stringify(g_maps);
		params += '&index_list=' + JSON.stringify(choice_maps);
	}

	if(StopFilepath != 'none')
	{
		params += '&stop_script_exe_name=' + $('#stop_script_exe_name').val();
		params += '&stop_script_exe_params=' + $('#stop_script_exe_params').val();
		params += '&stop_script_work_path=' + $('#stop_script_work_path').val();
		params += '&stop_script_unzip_path=' + $('#stop_script_unzip_path').val();
		params += '&stop_script_zip_path='+StopFilepath;
	}

	if(StartFilepath != 'none')
	{
		params += '&start_script_exe_name=' + $('#start_script_exe_name').val();
		params += '&start_script_exe_params=' + $('#start_script_exe_params').val();
		params += '&start_script_work_path=' + $('#start_script_work_path').val();
		params += '&start_script_unzip_path=' + $('#start_script_unzip_path').val();
		params += '&start_script_zip_path='+StartFilepath;
	}

	params += '&op_type='+g_op_type;
	params += '&edit_plan_id='+$('#edit_plan_id').val();
	var immediately = $('#finishFormDiv').find('input[name="immediately"]:checked').val();
	params += '&immediately='+immediately;
	myAjaxPost('../hotbackup_handle/?a=create_hotbackup_plan',params,restoresuccess,immediately);

}

function OnUploadFile(form,callback,StopFilepath)
{
	if (form == 'StopScriptForm')
	{
		if($('#stop_script_zip').val() == '')
		{
			callback('none');
			return;
		}
	}

	if (form == 'StartScriptForm')
	{
		if($('#start_script_zip').val() == '')
		{
			callback('none',StopFilepath);
			return;
		}
	}

	var postdata = new FormData($("#"+form)[0]);
	$.ajax({
            url: '../hotbackup_handle/?a=upload_script',
            type: 'POST',
            cache: false,
            data: postdata,
            dataType: 'json',
            processData: false,
            contentType: false,
            beforeSend: function (xhr, settings) {
                var csrftoken = $.cookie('csrftoken');
                xhr.setRequestHeader("X-CSRFToken", csrftoken);
				$('#uploadmsg').html('<img src="/static/images/loading.gif" height="30" width="30" /> 上传中，请稍侯...');
            }
        }).done(function (res) {
            if (res.r==0) {
                $('#uploadmsg').html('上传完成');
				if (form == 'StopScriptForm')
				{
					callback(res.filepath);
				}
				else if (form == 'StartScriptForm')
				{
					callback(res.filepath,StopFilepath);
				}
            }
            else {
				openErrorDialog({title:'错误',html:res.e});
				$('#uploadmsg').html('上传失败,'+res.e);
            }
        }).fail(function (res) {
			$('#uploadmsg').html('上传失败');
    });
}

function StopScriptFormCallback(StopFilepath)
{
	OnUploadFile('StartScriptForm',StartScriptFormCallback,StopFilepath);
}

function StartScriptFormCallback(StartFilepath,StopFilepath)
{
	OnHotBackup(StartFilepath,StopFilepath);
}

function Onfinish()
{
	$("#finishFormDiv").attr('title','完成').dialog({
		autoOpen: true,
		height: 200,
		width: 400,
		modal: true,
		buttons: {
			'确定': function(){
				$(this).dialog('close');
				OnUploadFile('StopScriptForm',StopScriptFormCallback,'none');
			},
			'取消': function(){
				$(this).dialog('close');
			}
		},
		close: function(){
			$(this).dialog('close');
		}
	});
}

function src_is_linux() {
	return parseInt($('#is_windows').val()) ? false : true;
}

$('#next').click(function() {
	if($('.pe_mywaite').is(":visible"))
	{
		return;
	}

    if(isShowedCheckingPeMsg()){
        return;
    }

	clearInterval(rt_t1);
	var selindex=$("#tabs").tabs('option', 'active');
	if(!CheckData(selindex))
	{
		return;
	}

	if (selindex == 1){
	    disk_checker.init();
    }

    if(selindex === 1 && isTargetPe('Servers_Tree2')){
        checkingPeStatus($("#tabs"), selindex + 1, getaciTreeChecked('Servers_Tree2'), function () {});
        return;
    }

	if (selindex == "2"){
		var point_id = get_point_id(),
            restore_time = get_restore_time();
        if (!get_point_id()){
            openErrorDialog('错误', '请选择需要同步到的备份点。')
            return;
        }
        if (!_isTime(get_restore_time())){
            openErrorDialog({title:'错误',html:"请填写正确的时间格式：YYYY-MM-DD hh:ss:ii.dddddd"});
            return;
        }
		var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
		if(switch_change_master_ip==1)
		{
			//启用IP漂移
			$( "#ip_switch_tabs" ).tabs( "option", "active", 1 );
		}
		else
		{
			$( "#ip_switch_tabs" ).tabs( "option", "active", 0 );
		}
	}

	if(selindex=="4")
	{
		//恢复方式
		var restoretype = $("input[name='restoretype']:checked").val();
		if(restoretype=="1")
		{
			$( "#tabs" ).tabs( "option", "active", 6);
			return;
		}
		// 选择了卷恢复
		var destserverid=getaciTreeChecked('Servers_Tree2');
		var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
		if (tmpid == 'ui_2'){
			openErrorDialog('错误', '选择卷恢复，目标只能是客户端，不能是启动介质。请返回目标选择界面，选择客户端。')
			return;
		}
	}
	if(selindex=="5")
	{
		//目标卷
		var restoretype = $("input[name='restoretype']:checked").val();
		if(restoretype=="2")
		{
			var choose = $('#disk_vol_div input:checked').val();
			if(choose == undefined){
		    	openErrorDialog('错误','请勾选对应的卷目标。');
		    	return;
			}
			$( "#tabs" ).tabs( "option", "active", 10);
			return;
		}
	}
	if(selindex=="6")
	{
		//平台检测
		var f1 = $('#check_left td').hasClass('status_waiting_fonts');
		var trs = $('#check_left tr');
		var re = /\(/;
		if (f1){ //正在进行任务，不可以跳转。
		    return;
        }
        else{
		    $.each(trs, function (index, item) {
            var td_html = $(item).children().last().html();
            var rs = re.exec(td_html);
            if (rs != null){
                $(item).children().last().html(td_html.slice(0,rs.index)); //去掉后面的计时括号
                }
            })
			if (src_is_linux()){
		    	$( "#tabs" ).tabs( "option", "active", 9 );
		        return;
			}
		    if (g_same){ //是相同平台
		        $( "#tabs" ).tabs( "option", "active", 8 );
		        return;
            }
            else {
		        $( "#tabs" ).tabs( "option", "active", 7 );
		        return;
            }
        }
	}
	if (selindex == 7){
		var driverfileid=getAciTreeBoxChecked('drivers_Tree');
		if(driverfileid)
		{
		    var content = $('<div></div>');
		    var p = $('<p style="margin-top:10px;text-indent:2em"></p>').text('在"{{ company }}在线驱动库"中找到与恢复目标机硬件匹配的驱动，点击"导入驱动"按钮，做驱动更新。');
		    var p1 = $('<p style="margin-top:10px;"></p>').html('注：<span style="color:red">如果放弃驱动导入，恢复后目标机可能无法启动。</span>');
			content.append(p).append(p1);
			openConfirmDialog({
				title:'导入驱动',
				html:content,
				height:270,
				width:500,
				confirm_text:'导入驱动',
				onBeforeOK:function(){
					myupdate();
					$(this).dialog('close');
				},
				onCancel:function(){
                    $( "#tabs" ).tabs( "option", "active", 8 );
					return;
				}
			});
			return;
		}
		if (is_no_driver()){
		     notify_no_driver();
		    return;
        }
	}
	if(selindex==8)
	{
		if ($('#driver_update_method input:checked').val() == 2){
	        ret = has_choice_driver();
		    if(ret.r!=0)
			{	var html = '设备';
				html += ret.driverAray.join(',');
				html += '没有勾选对应的驱动！你确定忽略吗?';
				openConfirmDialog({
					title:'确认信息',
					html:html,
					onBeforeOK:function(){
						$( "#tabs" ).tabs( "option", "active", selindex+1 );
						$(this).dialog('close');
					}
				});
				return;
			}
        }
	}
	if (selindex == 9){
	    if (!verify_disks()){
	        return;
        }
    }
	if(selindex==10)
	{
		Onfinish();
	}
	else
	{
		$( "#tabs" ).tabs( "option", "active", selindex+1 );
	}
});

$('#prev').click(function() {
	if($('.pe_mywaite').is(":visible"))
	{
		return;
	}
	clearInterval(rt_t1);
	var selindex=$("#tabs").tabs('option', 'active');
	if(selindex=="6")
	{
		//平台检测
		var f1 = $('#check_left td').hasClass('status_waiting_fonts');
		if (f1){ //正在进行任务，不可以跳转。
		    return;
        }
		var restoretype = $("input[name='restoretype']:checked").val();
		if(restoretype=="1")
		{
			$( "#tabs" ).tabs( "option", "active", 4);
			return;
		}
	}
	if (selindex == "9"){
		if (src_is_linux()){
			$( "#tabs" ).tabs( "option", "active", selindex-3 );
			return;
		}
	}
	if(selindex=="8")
	{
		if(g_same){
	        $( "#tabs" ).tabs( "option", "active", selindex-2 );
        }
        else{
	        $( "#tabs" ).tabs( "option", "active", selindex-1 );
        }
		return;
	}
	if(selindex=="10")
	{
		var restoretype = $("input[name='restoretype']:checked").val();
		if(restoretype=="2")
		{
			$( "#tabs" ).tabs( "option", "active", 5);
			return;
		}
	}
	$( "#tabs" ).tabs( "option", "active", selindex-1 );
});

function change_all_to_todo(){
    var trs = $('#check_left tr');
    $.each(trs, function (index, item) {
        var childs = $(item).children();
        $(childs[0]).attr('class','status_todo_icon');
        $(childs[1]).attr('class','');
        $(childs[2]).html('未开始');
    })
}

$('#HardDisks').on('click', '.header_button', function () {
    var tbody = $(this).parents('.disk_table_container').find('.disk_item_body');
    if (tbody.is(':visible')){
        tbody.hide('blind', 800);
        $(this).text('►');
    }
    else{
        tbody.show('blind', 800);
        $(this).text('▼');
    }
});

$('#HardDisks').on('click', '.header_check', function () {
    if ($(this).is('.isbootable')){
        return;
    }
    var child_inputs = $(this).parents('.disk_table_container').find('.disk_item_body input[name=current]');
    if ($(this).is(':checked')){
        child_inputs.prop('checked', true);
        child_inputs.each(function () {
            change_same_vol_status(this);
        })
    }
    else{
        child_inputs.prop('checked', false);
        child_inputs.each(function () {
            change_same_vol_status(this);
        })
    }
});

function change_same_vol_status(obj){
    var status = $(obj).prop('checked');
    var vol_name = $(obj).attr('vol_name');
    $('.disk_item_body input').each(function () {
        if ($(this).attr('vol_name') == vol_name){
            $(this).prop('checked', status);
            var div_container = $(this).parents('.disk_table_container');
            if (status){
                div_container.find('.header_check').prop('checked', true);
                div_container.find('.disk_item_body').show('blind', 800);
                div_container.find('.header_button').text('▼');
            }
        }
    });
}

$('#HardDisks').on('click', '.disk_item_body input', function () {
    change_same_vol_status(this);
});

$('#tabs-9').on('click', '.set_disk_step_button', function () {
    var tbody = $(this).parent().siblings();
    if (tbody.is(':visible')){
        tbody.hide('blind', 800);
        $(this).text('►');
    }
    else{
        tbody.show('blind', 800);
        $(this).text('▼');
    }
});

function GetBackupPoint(jsonstr,newTabIndex)
{

	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
    jQuery("#point_list").jqGrid("clearGridData");
	if(jsonstr.list.length>0)
	{
		s_d_vue.time = jsonstr.st_date;
		s_d_vue.moment_obj = moment(jsonstr.st_date);
		for(var i=0;i<jsonstr.list.length;i++)
		{
			var id = jsonstr.list[i].id;
			var time = jsonstr.list[i].time;
			var type = jsonstr.list[i].type;
			var recommend = jsonstr.list[i].recommend;
			if(recommend==false)
			{
				recommend='否';
			}
			else
			{
				recommend='是';
			}
			var tmp = {id:id,point_id:id,time:time,type:type,recommend:recommend};
			jQuery("#point_list").jqGrid('addRowData',i+1,tmp);
		}

		jQuery("#point_list").trigger("reloadGrid");
		//已排序，得到的是最新备份点
		$('#point_time').html(jsonstr.list[0].enddate.replace(/T/g, ' '));
		$('#pointid').val('nonone');
		latest_point = jsonstr.list[0].id; //保留这个ID
		$("#tabs").tabs( "option", "active", newTabIndex);
		$('#is_windows').val(jsonstr.is_windows ? 1: 0);
		return
	}
	else
	{
        var msg = '<p style="margin: 0;text-indent: 2em">客户端创建热备计划需要至少有一个已经备份完成的可使用备份点。</p>\
                    <p style="margin: 0;text-indent: 2em">此客户端没有备份点或备份点正在备份中，无可使用备份点。</p>\
                    <p style="margin: 0;text-indent: 2em">请先为此客户端创建备份计划，并等待备份完成后再创建热备计划。 </p>'
		openErrorDialog({title:'错误',html:msg, width: 450, height: 250});
		return;
	}
}

function is_dest_host_in_plan(jsonstr,newTabIndex)
{
	g_have_check_dest_host_in_plan = true;
	if(jsonstr.r!=0)
	{
        $("#restoreVolFormDiv").dialog('close');
		openErrorDialog({title:'错误',html:jsonstr.e});
		g_have_check_dest_host_in_plan = false;
		return;
	}
	$( "#tabs" ).tabs( "option", "active", newTabIndex);
}

$('#tabs').on('tabsbeforeactivate', function (event, ui) {
	var oldTabIndex = ui.oldTab.index();
	var newTabIndex = ui.newTab.index();
	if( g_op_type=='create_plan' && oldTabIndex == 0 && newTabIndex == 1 )
	{
		var pointid = $('#pointid').val();
		if( pointid == 'none')
		{
			$('#stable_point_container').hide();
			s_d_vue.host_ident = getaciTreeChecked('Servers_Tree1');
			s_d_vue.init();
			$('#cdp_ui_id').hide();
			var params = 'host_ident='+getaciTreeChecked('Servers_Tree1');
			params += '&checkident=1';
			myAjaxGet('../hotbackup_handle/?a=get_point_list',params,GetBackupPoint,newTabIndex);
			return false;
		}
	}
	if( oldTabIndex == 1 && newTabIndex == 2 )
	{
		var destserverid=getaciTreeChecked('Servers_Tree2');
		var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
		if(tmpid=='ui_1')
		{
			PE_ID=GetAciTreeValueRadio('Servers_Tree2',destserverid,'peserverid');
			if(PE_ID==undefined)
			{
				$('.pe_mywaite').show();
				params="a=getserverid&agentserverid="+destserverid;
				myAjaxGet('../migrate_handle/',params,GetServerId,newTabIndex);
				return false;
			}else{
                if(g_op_type=='create_plan' && g_have_check_dest_host_in_plan == false) // 验证目标机是否已经计划中
                {
                    var params = 'a=is_dest_host_in_plan';
                    params += '&ident='+PE_ID;
                    myAjaxGet('../hotbackup_handle/',params,is_dest_host_in_plan,newTabIndex);
                    return false;
                }
            }
		}
		else
		{
			if(g_op_type=='create_plan' && g_have_check_dest_host_in_plan == false)
			{
				var params = 'a=is_dest_host_in_plan';
				params += '&ident='+destserverid;
				myAjaxGet('../hotbackup_handle/',params,is_dest_host_in_plan,newTabIndex);
				return false;
			}
		}
	}
	if (oldTabIndex <= 7 && newTabIndex == 8) {
        if (src_is_linux()) {
            $('#driver_update_method input[value=1]').click();
        } else {
            if (is_same() && is_restore_to_self()) {
                $('#driver_update_method input[value=1]').click();
            } else {
                $('#driver_update_method input[value=2]').click();
                check_multi_version = true;
            }
        }
    }
	if( newTabIndex == 10 )
	{
		$('#uploadmsg').html('');
	}
});

function myDrawHotBackup()
{
	var switchtype=$("input[name='"+getswitchtype_input_name()+"']:checked").val();
	var ip_switch_tabs_div_id = get_ip_switch_tabs_div_id();
	if(switchtype == 1 )
	{
		$('div#'+ip_switch_tabs_div_id+' .auto_switch_div').hide();
		DrawHotBackup();
	}
	else
	{
		$('div#'+ip_switch_tabs_div_id+' .auto_switch_div').show();
		DrawHotBackup();
	}
}

$('#tabs').on('tabsactivate', function (event, ui) {
	var newPanel = ui.newPanel.selector;
	var oldPanel = ui.oldPanel.selector;
	if(newPanel == '#tabs-0' )
	{
		$('#prev').hide();
	}
	else
	{
		$('#prev').show();
	}

	if(newPanel == '#tabs-10')
	{
		$('#next').attr("value","完成");
	}
	else
	{
		$('#next').attr("value","下一步»");
	}

	switch (newPanel) {
		case '#tabs-0':
			break;
		case '#tabs-1':
			g_have_check_dest_host_in_plan = false;
			RefreshAciTree("Servers_Tree2",'../restore_handle/?a=getrestoreserverlist&id=');
			break;
		case '#tabs-2':
			if(g_op_type=='create_plan')
			{
				var hostName = getaciTreeNameChecked('Servers_Tree1'); // 使用源主机
				var planName = '热备' + hostName + CurentTime();
				$('#taskname').val(planName);
			}
		case '#tabs-3':
			myDrawHotBackup();
			break;
		case '#tabs-5':
			break;
		case '#tabs-9':
			var pointid=get_point_id();
			params="pointid="+pointid;
			var destserverid=getaciTreeChecked('Servers_Tree2');
			var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
			if(tmpid=='ui_1')
			{
				destserverid=GetAciTreeValueRadio('Servers_Tree2',destserverid,'peserverid');
			}
			$('#msg').html('正在获取硬盘信息。');
			$('.mywaite').show();
			params+="&destserverid="+destserverid;
			$('div#HardDisks').empty();
			$('#current_point_time').text('');
			myAjaxGet('../restore_handle/?a=harddisksettings&disk_type=1',params,GetHardDisks);
			break;
	}

	if( oldPanel == '#tabs-1' && newPanel == '#tabs-2')
	{
		var ident=getaciTreeChecked('Servers_Tree1');
		var params = 'a=getAdapterInfo&type=host&ident='+ident;
		params += '&pointid='+get_point_id();
		$("input[name='ip_switch_tabs_0_switchtype'][value='2']").prop('disabled',true);
		$("input[name='ip_switch_tabs_1_switchtype'][value='2']").prop('disabled',true);
		myAjaxGet('../hotbackup_handle/',params,SetAdpterInfo,'master');
	}

	if(oldPanel == '#tabs-4' && newPanel == '#tabs-5')
	{
		var destserverid=getaciTreeChecked('Servers_Tree2');
		var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
		if(tmpid=='ui_1')
		{
			$('#msg_vol').text('分配资源中...');
			$('.mywaite_vol').show();
			volRefreshSrcServer();
		}
	}
	else if(oldPanel == '#tabs-4' && newPanel == '#tabs-6')
	{
		g_same = false;
		has_set_interval=false;
		change_all_to_todo();
		var destserverid=getaciTreeChecked('Servers_Tree2');
		var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);

		if (src_is_linux()){
			change_to_checked(1, '已分配');
			return;
		}

		if(tmpid=='ui_1')
		{
			change_to_waiting(1);
			change_to_waiting(2, '已分配');
			destserverid=GetAciTreeValueRadio('Servers_Tree2',destserverid,'peserverid');
			check_is_same(destserverid);
		}
		else
		{
		    change_to_waiting(2,'已分配');
			CheckPEDriver();
		}
	}
});

function CheckPEDriver()
{

    var destserverid=getaciTreeChecked('Servers_Tree2');
    var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
    if(tmpid=='ui_1')
    {
        destserverid=GetAciTreeValueRadio('Servers_Tree2',destserverid,'peserverid');
    }
	check_is_same(destserverid);
}


var g_paper_0 = new Raphael('switch_canvas_0', 500, 300);
var g_paper_1 = new Raphael('switch_canvas_1', 500, 300);
var g_paper = g_paper_0;
var g_arbitrate_ip_input = null;
var g_master_control_ip_input = null;
var g_standby_control_ip_input = null;
var g_master_service_ip_input = null;
var g_standby_service_ip_input = null;

function CreateInput(x,y,text)
{
	var input = null;
	var tip_txt = '未配置';
	var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
	if(switch_change_master_ip==1)
	{
		g_paper = g_paper_1;
	}
	else
	{
		g_paper = g_paper_0;
	}
	if(text==undefined)
	{
		text = '';
	}
	if(text=='')
	{
		input = g_paper.text(x,y,tip_txt);
		input.attr({"font-size":"12px","font-style":"italic","text-anchor":"start"});
	}
	else
	{
		input = g_paper.text(x,y,text);
		input.attr({"font-size":"12px","text-anchor":"start"});
	}
	return input;
}

function DrawHotBackup_0()
{
	g_paper.clear();
	var switchtype=$("input[name='"+getswitchtype_input_name()+"']:checked").val();
	if(switchtype == 2)
	{
		g_paper.image("/static/images/pc.png", 223, 12, 30, 38);
		g_paper.text(235,58,'仲裁服务器').attr({"font-size":"12px"});
		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_arbitrate_2_master_business_ip').prop('checked'))
		{
			g_paper.path("M235,88 L237,235");
			$('#arbitrate_div').show();
			var arbitrate_ip = $('div#'+get_ip_switch_tabs_div_id()+' input[name=arbitrate_ip]').val();
			g_paper.text(210,72,'IP：').attr({"font-size":"12px"});
			g_arbitrate_ip_input = CreateInput(225,72,arbitrate_ip);
		}
		else
		{
			g_paper.path("M235,70 L237,235");
			$('#arbitrate_div').hide();
		}

		g_paper.path("M175,155 Q230,130 230,68");
	}
	else
	{
		$('#arbitrate_div').hide();
		g_paper.text(250,58,'手工切换').attr({"font-size":"15px"});
		g_paper.path("M176,164 Q226,180 226,233");
	}

	g_paper.image("/static/images/pc.png", 125, 115, 30, 38);

	g_paper.text(145,165,'主服务器').attr({"font-size":"12px"});
	g_paper.text(95,185,'　　IP：').attr({"font-size":"12px"});;

	g_paper.image("/static/images/pc.png", 339, 115, 30, 38);
	g_paper.text(350,165,'备服务器').attr({"font-size":"12px"});

	g_paper.text(320,185,'　　IP：').attr({"font-size":"12px"});
	//g_paper.text(320,205,'　　IP：').attr({"font-size":"12px"});

	g_paper.image("/static/images/pc.png", 224, 240, 30, 38);
	g_paper.text(240,285,'备份一体机').attr({"font-size":"12px"});

	var standby_adpter = $.evalJSON(GetAdpterSettings_not_switch_ip('not_switch_ip_adptersettings_div'));

	var master_adpter = $.evalJSON(GetAdpterSettings('master_adptersettings_div'));

	var master_service_ip = '';
	var count = 0;
	for(var i=0;i<master_adpter.business.length;i++)
	{
		for(var j=0;j<master_adpter.business[i].ips.length>0;j++)
		{
			if(isIPV4(master_adpter.business[i].ips[j].ip))
			{
				count ++;
				if(master_service_ip == '')
				{
					master_service_ip = master_adpter.business[i].ips[j].ip;
				}
			}
		}
	}
	if(count>1)
	{
		master_service_ip += ' ('+count+')';
	}

	var standby_control_ip = '';
	for(var i=0;i<standby_adpter.control.length;i++)
	{
		for(var j=0;j<standby_adpter.control[i].ips.length>0;j++)
		{
			if(isIPV4(standby_adpter.control[i].ips[j].ip))
			{
				standby_control_ip = standby_adpter.control[i].ips[j].ip;
				break;
			}
		}
	}

	var standby_service_ip = '';
	var count = 0;
	for(var i=0;i<standby_adpter.business.length;i++)
	{
		for(var j=0;j<standby_adpter.business[i].ips.length>0;j++)
		{
			if(isIPV4(standby_adpter.business[i].ips[j].ip))
			{
				count++;
				if(standby_service_ip=='')
				{
					standby_service_ip = standby_adpter.business[i].ips[j].ip;
				}
			}
		}
	}
	if(count>1)
	{
		standby_service_ip += ' ('+count+')';
	}

	g_master_service_ip_input = CreateInput(115,185,master_service_ip);

	g_standby_control_ip_input = CreateInput(340,185,standby_control_ip);
	//g_standby_service_ip_input = CreateInput(340,205,standby_service_ip);

	g_paper.path("M247,233 Q260,180 318,163");

	if(switchtype == 2)
	{

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_arbitrate_2_master_business_ip').prop('checked'))
		{
		}

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_control_ip').prop('checked'))
		{
			g_paper.path("M177,163 Q227,179 227,232").attr({"stroke":"#0b7505"});
		}

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_business_ip').prop('checked'))
		{
			g_paper.path("M175,165 Q225,181 225,234").attr({"stroke":"#134868"});
		}
	}
}

function DrawHotBackup_1()
{
	g_paper.clear();
	var switchtype=$("input[name='"+getswitchtype_input_name()+"']:checked").val();
	if(switchtype == 2)
	{
		g_paper.image("/static/images/pc.png", 223, 12, 30, 38);
		g_paper.text(235,58,'仲裁服务器').attr({"font-size":"12px"});
		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_arbitrate_2_master_business_ip').prop('checked'))
		{
			g_paper.path("M235,88 L237,235");
			$('#arbitrate_div').show();
			var arbitrate_ip = $('div#'+get_ip_switch_tabs_div_id()+' input[name=arbitrate_ip]').val();
			g_paper.text(210,72,'IP：').attr({"font-size":"12px"});
			g_arbitrate_ip_input = CreateInput(225,72,arbitrate_ip);
		}
		else
		{
			g_paper.path("M235,70 L237,235");
			$('#arbitrate_div').hide();
		}

		g_paper.path("M175,155 Q230,130 230,68");
	}
	else
	{
		$('#arbitrate_div').hide();
		g_paper.text(250,58,'手工切换').attr({"font-size":"15px"});
		g_paper.path("M176,164 Q226,180 226,233");
	}

	g_paper.image("/static/images/pc.png", 125, 115, 30, 38);
	if ($('#master_server_fieldset').is(':visible'))
	{
		g_paper.text(145,165,'主服务器').attr({"font-size":"12px"});
		g_paper.text(95,185,'固有IP：').attr({"font-size":"12px"});
		g_paper.text(95,205,'漂移IP：').attr({"font-size":"12px"});
	}
	else
	{

		g_paper.text(145,165,'远程服务器').attr({"font-size":"12px"});

	}
	g_paper.image("/static/images/pc.png", 339, 115, 30, 38);
	g_paper.text(350,165,'备服务器').attr({"font-size":"12px"});

	g_paper.text(320,185,'固有IP：').attr({"font-size":"12px"});
	g_paper.text(320,205,'漂移IP：').attr({"font-size":"12px"});

	g_paper.image("/static/images/pc.png", 224, 240, 30, 38);
	g_paper.text(240,285,'备份一体机').attr({"font-size":"12px"});

	var standby_adpter = $.evalJSON(GetAdpterSettings('standby_adptersettings_div'));



	var master_adpter = $.evalJSON(GetAdpterSettings('master_adptersettings_div'));
	var master_control_ip = '';
	for(var i=0;i<master_adpter.control.length;i++)
	{
		for(var j=0;j<master_adpter.control[i].ips.length>0;j++)
		{
			if(isIPV4(master_adpter.control[i].ips[j].ip))
			{
				master_control_ip = master_adpter.control[i].ips[j].ip;
				break;
			}
		}
	}

	var master_service_ip = '';
	var count = 0;
	for(var i=0;i<master_adpter.business.length;i++)
	{
		for(var j=0;j<master_adpter.business[i].ips.length>0;j++)
		{
			if(isIPV4(master_adpter.business[i].ips[j].ip))
			{
				count ++;
				if(master_service_ip == '')
				{
					master_service_ip = master_adpter.business[i].ips[j].ip;
				}
			}
		}
	}
	if(count>1)
	{
		master_service_ip += ' ('+count+')';
	}

	var standby_control_ip = '';
	for(var i=0;i<standby_adpter.control.length;i++)
	{
		for(var j=0;j<standby_adpter.control[i].ips.length>0;j++)
		{
			if(isIPV4(standby_adpter.control[i].ips[j].ip))
			{
				standby_control_ip = standby_adpter.control[i].ips[j].ip;
				break;
			}
		}
	}

	var standby_service_ip = '';
	var count = 0;
	for(var i=0;i<standby_adpter.business.length;i++)
	{
		for(var j=0;j<standby_adpter.business[i].ips.length>0;j++)
		{
			if(isIPV4(standby_adpter.business[i].ips[j].ip))
			{
				count++;
				if(standby_service_ip=='')
				{
					standby_service_ip = standby_adpter.business[i].ips[j].ip;
				}
			}
		}
	}
	if(count>1)
	{
		standby_service_ip += ' ('+count+')';
	}

	if ($('#master_server_fieldset').is(':visible'))
	{
		g_master_control_ip_input = CreateInput(115,185,master_control_ip);
		g_master_service_ip_input = CreateInput(115,205,master_service_ip);
	}
	g_standby_control_ip_input = CreateInput(340,185,standby_control_ip);
	g_standby_service_ip_input = CreateInput(340,205,standby_service_ip);

	g_paper.path("M247,233 Q260,180 318,163");

	if(switchtype == 2)
	{

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_arbitrate_2_master_business_ip').prop('checked'))
		{
		}

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_control_ip').prop('checked'))
		{
			g_paper.path("M177,163 Q227,179 227,232").attr({"stroke":"#0b7505"});
		}

		if($('div#'+get_ip_switch_tabs_div_id()+' #detect_aio_2_master_business_ip').prop('checked'))
		{
			g_paper.path("M175,165 Q225,181 225,234").attr({"stroke":"#134868"});
		}
	}
}


function DrawHotBackup()
{
	var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
	if(switch_change_master_ip==1)
	{
		g_paper = g_paper_1;
		DrawHotBackup_1();
	}
	else
	{
		g_paper = g_paper_0;
		DrawHotBackup_0();
	}
}


function onblur_drawHotBackup(obj) {
    if(obj.value == "0.0.0.0"){
        obj.value = ''
    }
	removespace(obj);
	DrawHotBackup();
}

$('#RefreshSrcServer').button().click(function(){
	RefreshAciTree("Servers_Tree1",'../backup_handle/?a=getlist&include_remote_host=1&id=');
});

$('#RefreshDestServer').button().click(function(){
	RefreshAciTree("Servers_Tree2",'../restore_handle/?a=getrestoreserverlist&id=');
});

function GetServerInfoCallback(jsonstr)
{
	if(jsonstr.r!=0)
	{
		$('#show_servername').html(jsonstr.e);
		return;
	}
	$('#show_servername').html(jsonstr.servername);
	$('#show_pcname').html(jsonstr.pcname);
	$('#show_ip').html(jsonstr.ip);
	$('#show_mac').html(jsonstr.mac);
	$('#show_os').html(jsonstr.os);
	$('#show_buildnum').html(jsonstr.buildnum);
	$('#show_harddisknum').html(jsonstr.harddisknum);
	$('#show_harddiskinfo').html(jsonstr.harddiskinfo);
	$('#show_total').html(jsonstr.total + 'GB');
	$('#show_use').html(jsonstr.use + 'GB');
	if(jsonstr.network_transmission_type == 1 )
	{
		$('#isencipher').val('加密');
	}
	else
	{
		$('#isencipher').val('不加密');
	}
}

// 获取主机信息
$('#Servers_Tree1').on('acitree', function(event, api, item, eventName, options)
{
	if(eventName!='selected')
	{
		return;
	}
	if(g_op_type=='create_plan')
	{
		$('#pointid').val('none');
	}
	var id=api.getId(item);
	if(id!=undefined && id.substring(0,3)!='ui_')
	{
	    $('#show_servername').html('');
        $('#show_pcname').html('');
        $('#show_ip').html('');
        $('#show_mac').html('');
        $('#show_os').html('');
        $('#show_buildnum').html('');
        $('#show_harddisknum').html('');
        $('#show_harddiskinfo').html('');
        $('#show_total').html('');
        $('#show_use').html('');
		var params="a=getserverinfo&id="+id;
		myAjaxGet('../backup_handle/',params,GetServerInfoCallback);
	}
});

function change_to_waiting(id, result) {
    var obj = $('#step' + id);
    var childs = obj.children();
    $(childs[0]).attr('class','status_waiting_icon');
    $(childs[1]).attr('class','status_waiting_fonts');
    $(childs[2]).html('正在进行');
    var obj_brother = $('#step' + (id-1));
    if (obj_brother.length > 0){
        var childs_b = obj_brother.children();
        $(childs_b[0]).attr('class','status_checked_icon');
        $(childs_b[1]).attr('class','');
        $(childs_b[2]).html(result);
    }
}

function addIP(ips_div,control_id)
{
	if(control_id == 'standby')
	{
		var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
		if(switch_change_master_ip==0)
		{
			$('#'+ips_div).append('<div style="margin-top:10px;">　<span name="ip_span">　　IP</span>：<input type="text" name="service_ip" onblur="onblur_drawHotBackup(this)"/></div>');
		}
		else
		{
			$('#'+ips_div).append('<div style="margin-top:10px;">　<span name="ip_span">漂移IP</span>：<input type="text" name="service_ip" onblur="onblur_drawHotBackup(this)"/></div>');
		}
	}
	else if(control_id == 'ip_not_switch_dns')
	{
		$('#'+ips_div).append('<div style="margin-top:10px;">　<span name="ip_span">　　IP</span>：<input type="text" name="service_ip" onblur="onblur_drawHotBackup(this)"/></div>');
	}
	else
	{
		$('#'+ips_div).append('<div style="margin-top:10px;">　漂移IP：<input type="text" name="service_ip" onblur="onblur_drawHotBackup(this)"/></div>');
	}
	$('#'+ips_div).append('<div style="margin-top:10px;margin-bottom:10px;">子网掩码：<input type="text" name="route_mask" onblur="onblur_drawHotBackup(this)"/></div>');
}

function addDNS(dns_div,control_id)
{
	if(control_id == 'master')
	{
		master_dns_index++;
		$('#'+dns_div).append('<div style="margin-top:10px;margin-left: 15px">DNS'+master_dns_index+'：<input type="text"' +
            ' name="dns"/></div>');
	}
	else if(control_id == 'standby')
	{
		standby_dns_index++;
		$('#'+dns_div).append('<div style="margin-top:10px;margin-left: 15px">DNS'+standby_dns_index+'：<input type="text" name="dns"/></div>');
	}
	else if(control_id == 'ip_not_switch_dns')
	{
		ip_not_switch_dns_index++;
		$('#'+dns_div).append('<div style="margin-top:10px;margin-left: 15px">DNS'+ip_not_switch_dns_index+'：<input type="text" name="dns"/></div>');
	}
	else
	{
		debugger;
		alert('addDNS Failed.control_id='+control_id);
	}

}

function on_adpter_sel_change(obj,id)
{
	var mac = $(obj).val().split('|')[1];
	$("div#"+get_ip_switch_tabs_div_id()+" #"+id+" input[name=control_mac]").val(mac);
}

function Set_not_ip_switch_AdpterInfo(jsonstr)
{
	//var dns_id='ip_not_switch_dns_div';
	var adpters = jsonstr.list;
	var class_adpter_div_obj = $('div#ip_switch_tabs_0 .class_adpter_div');
	class_adpter_div_obj.empty();
	for(var i=0;i<adpters.length;i++)
	{
		class_adpter_div_obj.append('<div style="margin-top:10px;">　　网卡：<input type="hidden" name="adapter_id" value="'+adpters[i].adapter.id+'" /> <input type="text" class="text6" style="width:180px;" value="'+adpters[i].adapter.name+'" disabled="disabled"/></div>');
		class_adpter_div_obj.append('<div style="margin-top:10px;margin-left:23px;">MAC：<input type="text" value="'+adpters[i].adapter.mac+'" class="text6" style="width:190px;" name="mac" disabled="disabled"/></div>');
		var ips=adpters[i].ips;
		var ips_div='ip_not_switch'+adpters[i].adapter.id+'_ips_div';
		ips_div = ips_div.replace(/{/, "_");
		ips_div = ips_div.replace(/}/, "_");
		class_adpter_div_obj.append('<div id="'+ips_div+'"></div>');
		for(var j=0;j<ips.length;j++)
		{
			$('#'+ips_div).append('<div style="margin-top:10px;">　　　IP：<input type="text" name="service_ip" value="'+ips[j].ip+'" onblur="onblur_drawHotBackup(this)"/></div>');
			$('#'+ips_div).append('<div style="margin-top:10px;margin-bottom:10px;">子网掩码：<input type="text" value="'+ips[j].mask+'" name="route_mask" onblur="onblur_drawHotBackup(this)"/></div>');
		}
		class_adpter_div_obj.append('<div style="border-bottom:1px solid #d3d3d3;margin-top:10px;padding-bottom:10px;"><span style="color: blue;cursor:pointer;margin-left:210px;" onclick="addIP(\''+ips_div+'\',\'ip_not_switch_dns\')">增加IP</span></div>');
	}

	// 填入对应的网关
    if (jsonstr.gate_way && isIPV4(jsonstr.gate_way)){
	    $('div#ip_switch_tabs_0 input[name=gateway]').val(jsonstr.gate_way);
    }else{
        $('div#ip_switch_tabs_0 input[name=gateway]').val('');
    }

	var dns=jsonstr.dns;
	var dns_div = 'ip_not_switch_dns_1';
	var dns_div_obj = $('div#ip_switch_tabs_0 .class_dns_div');
	dns_div_obj.html('<div id="'+dns_div+'"></div>');
	for(var k=0;k<dns.length;k++)
	{
		$('#ip_not_switch_dns_1').append('<div style="margin-top:10px;margin-left:15px;">DNS'+(k+1)+'：<input type="text" value="'+dns[k]+'" name="dns"/></div>');
	}
	dns_div_obj.append('<div style="margin-left:200px;margin-top:10px;padding-bottom:10px;"><span style="color: blue;cursor:pointer;" onclick="addDNS(\'ip_not_switch_dns_1\',\'ip_not_switch_dns\')">增加DNS</span></div>');
}

function SetAdpterInfo(jsonstr,control_id)
{
	if(jsonstr.r!=0)
	{
        $("#restoreVolFormDiv").dialog('close');
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}

	if(control_id=='master')
	{
		if(jsonstr.remote==1)
		{
			$("input[name='ip_switch_tabs_0_switchtype'][value='2']").prop('disabled',true);
			$("input[name='ip_switch_tabs_1_switchtype'][value='2']").prop('disabled',true);
			$('#master_server_fieldset').hide();
			$('#remote_server_fieldset').show();
			$('#switch_change_master_ip_div').hide();
		}
		else
		{
			$("input[name='ip_switch_tabs_0_switchtype'][value='2']").prop('disabled',false);
			$("input[name='ip_switch_tabs_1_switchtype'][value='2']").prop('disabled',false);
			$('#master_server_fieldset').show();
			$('#remote_server_fieldset').hide();
			$('#switch_change_master_ip_div').show();
		}
	}
	else
	{
		Set_not_ip_switch_AdpterInfo(jsonstr);
	}
	var sel_id=control_id+'_control_adpter';
	var mac_id=control_id+'_control_mac';
	var div_id=control_id+'_adpter_div';
	var dns_id=control_id+'_dns_div';
	var adpters = jsonstr.list;
	$("#"+sel_id).empty();
	$('#'+div_id).html('');
	for(var i=0;i<adpters.length;i++)
	{
		var id_mac = adpters[i].adapter.id+'|'+adpters[i].adapter.mac.split('（')[0];

		if (control_id == 'standby'){
		    if (adpters[i].adapter.isConnected){
		        $("#"+mac_id).val(adpters[i].adapter.mac);
		        $("#"+sel_id).append("<option value='"+id_mac+"'>"+adpters[i].adapter.name+"</option>");
		        $("#standby_control_ip").val(adpters[i].ips[0].ip);
		        $("#standby_route_mask").val(adpters[i].ips[0].mask);
            }
        }else{
		    if (i == 0){
		        $("#"+mac_id).val(adpters[i].adapter.mac);
            }
		    $("#"+sel_id).append("<option value='"+id_mac+"'>"+adpters[i].adapter.name+"</option>");
        }
		$('#'+div_id).append('<div style="margin-top:10px;">　　网卡：<input type="hidden" name="adapter_id" value="'+adpters[i].adapter.id+'" /> <input type="text" class="text6" style="width:180px;" value="'+adpters[i].adapter.name+'" disabled="disabled"/></div>');
		$('#'+div_id).append('<div style="margin-top:10px;margin-left:23px;">MAC：<input type="text" value="'+adpters[i].adapter.mac+'" class="text6" style="width:190px;" name="mac" disabled="disabled"/></div>');
		var ips=adpters[i].ips;
		var ips_div=control_id+adpters[i].adapter.id+'_ips_div';
		ips_div = ips_div.replace(/{/, "_");
		ips_div = ips_div.replace(/}/, "_");
		$('#'+div_id).append('<div id="'+ips_div+'"></div>');
		for(var j=0;j<ips.length;j++)
		{
		    if(control_id == 'standby'){
		        $('#'+ips_div).append('<div style="margin-top:10px;">　<span name="ip_span">漂移IP</span>：<input type="text" name="service_ip" value="" onblur="onblur_drawHotBackup(this)"/></div>');
			    $('#'+ips_div).append('<div style="margin-top:10px;margin-bottom:10px;">子网掩码：<input type="text" value="" name="route_mask" onblur="onblur_drawHotBackup(this)"/></div>');
            }else{
		        $('#'+ips_div).append('<div style="margin-top:10px;">　漂移IP：<input type="text" name="service_ip" value="'+ips[j].ip+'" onblur="onblur_drawHotBackup(this)"/></div>');
			    $('#'+ips_div).append('<div style="margin-top:10px;margin-bottom:10px;">子网掩码：<input type="text" value="'+ips[j].mask+'" name="route_mask" onblur="onblur_drawHotBackup(this)"/></div>');
            }
		}
		$('#'+div_id).append('<div style="border-bottom:1px solid #d3d3d3;margin-top:10px;padding-bottom:10px;"><span style="color: blue;cursor:pointer;margin-left:210px;" onclick="addIP(\''+ips_div+'\',\''+control_id+'\')">增加IP</span></div>');
	}

	// 填入对应的网关
    if (jsonstr.gate_way && isIPV4(jsonstr.gate_way)){
	    $('#' +control_id +'_adptersettings_div input[name=gateway]').val(jsonstr.gate_way);
    }else{
        $('#' +control_id +'_adptersettings_div input[name=gateway]').val('');
    }

	var dns=jsonstr.dns;
	var dns_div = dns_id+'_1';
	$('#'+dns_id).html('<div style="margin-top:10px;">DNS</div>');
	$('#'+dns_id).html('<div id="'+dns_div+'"></div>');
	for(var k=0;k<dns.length;k++)
	{
		$('#'+dns_div).append('<div style="margin-top:10px;margin-left:15px;">DNS'+(k+1)+'：<input type="text" value="'+dns[k]+'" name="dns"/></div>');
	}
	if(control_id == 'master')
	{
		master_dns_index=dns.length;
	}
	else if(control_id == 'standby')
	{
		standby_dns_index=dns.length;
		ip_not_switch_dns_index=dns.length;
	}

	$('#'+dns_id).append('<div style="margin-left:200px;margin-top:10px;padding-bottom:10px;"><span style="color: blue;cursor:pointer;" onclick="addDNS(\''+dns_div+'\',\''+control_id+'\')">增加DNS</span></div>');

	if(control_id == 'master')
	{
		var destserverid=getaciTreeChecked('Servers_Tree2');
		if (!destserverid){
			return;
		}
		var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
		if(tmpid=='ui_1')
		{
			destserverid=GetAciTreeValueRadio('Servers_Tree2',destserverid,'peserverid');
			var params = 'a=getAdapterInfo&type=pe&ident='+destserverid;
			myAjaxGet('../hotbackup_handle/',params,SetAdpterInfo,'standby');
		}
		else
		{
			var params = 'a=getAdapterInfo&type=pe&ident='+destserverid;
			myAjaxGet('../hotbackup_handle/',params,SetAdpterInfo,'standby');
		}
	}

	if(g_op_type=='change_plan' && control_id == 'standby')
	{
		//先获取网卡信息，然后再填写用户数据
		var params = 'a=getAllAdapterInfo&plan_id='+$('#edit_plan_id').val();
		myAjaxGet('../hotbackup_handle/',params,SetAllAdpterInfo);
	}
	else if(control_id == 'standby')
	{
		myDrawHotBackup();
	}
	if(control_id=='master' && jsonstr.remote==1)
	{
	    $('#'+control_id+'_control_ip').val('1.1.1.1');
        $('#'+control_id+'_route_mask').val('0.0.0.0');
        $('#master_adptersettings_div input').prop('disabled',true);
	}

    // 定高度后，增加新元素会溢出
/*	setTimeout(function() {
		var mf = 0;
		var rf = 0;
		if ($('#master_server_fieldset').is(':visible'))
		{
			mf = $('#master_server_fieldset').height();
		}
		else
		{
			rf = $('#remote_server_fieldset').height();
		}
		var sf = $('#standby_server_fieldset').height();
		var n = Math.max(mf,rf,sf)+'px';
		$('#master_server_fieldset').height(n);
		$('#remote_server_fieldset').height(n);
		$('#standby_server_fieldset').height(n);
	}, 1000);*/
}

function IsSelectHaveId(select_id,value)
{
	var ops = $("#"+select_id).find("option");
	for(var i=0;i<ops.length;i++)
	{
		if(value == ops[i].value)
		{
			return true;
		}
	}
	return false;
}

function set_business_ips(business,id,ips)
{
	for(var i=0;i<business.length;i++)
	{
		if(business[i].id==id)
		{
			for(var j=0;j<business[i].ips.length;j++)
			{
				if(ips.length > j)
				{
					ips[j].ip.value=business[i].ips[j].ip;
					ips[j].mask.value=business[i].ips[j].mask;
				}
				else
				{
					debugger;
					//控件（漂移IP）数量不够
					//应修正后台读取网卡信息部分代码 hotbackup.py getAdapterInfo
					//原因：后台IP没有设置下去或读出的网卡数据不正确
				}
			}
		}
	}

}

function SetAdpterInfoForEdit(jsonobj,control_id)
{
	var control = jsonobj.control;
	for(var i=0;i<control.length;i++)
	{
		var id = control[i].id;
		var mac = control[i].mac;
		var id_mac = id+'|'+mac;
		if(IsSelectHaveId(control_id+'_control_adpter',id_mac))
		{
			$('#'+control_id+'_control_adpter').val(id_mac);
			var ips = control[i].ips;
			for(var j=0;j<ips.length;j++)
			{
				var ip = ips[j].ip;
				var mask = ips[j].mask;
				$('#'+control_id+'_control_ip').val(ip);
				$('#'+control_id+'_route_mask').val(mask);
			}
		}
		else
		{
			$('#'+control_id+'_control_ip').val('');
			$('#'+control_id+'_route_mask').val('');
		}
	}

	var business = jsonobj.business;
	var last_adpter_id = null;
	var business_ips = [];
	var obj = $('#'+control_id+'_adptersettings_div').find('.class_adpter_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='adapter_id')
		{
			if(last_adpter_id!=null)
			{
				set_business_ips(business,last_adpter_id,business_ips);
				business_ips = [];
			}
			last_adpter_id=input_obj.value;
		}

		if(input_obj.name=='service_ip')
		{
			service_ip=input_obj;
		}

		if(input_obj.name=='route_mask')
		{
			route_mask=input_obj;
			business_ips.push({'ip':service_ip,'mask':route_mask});

		}
	}
	if(last_adpter_id!=null)
	{
		set_business_ips(business,last_adpter_id,business_ips);
	}

	var route = jsonobj.route;
	var route_ip = null;
	var route_mask = null;
	var route_gateway = null;
	var j=0;
	var obj = $('#'+control_id+'_adptersettings_div').find('.class_route_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='route_ip')
		{
			route_ip = input_obj;
		}
		if(input_obj.name=='route_mask')
		{
			route_mask = input_obj;
		}
		if(input_obj.name=='route_gate')
		{
			route_gateway = input_obj;
			route_ip.value = '';
			route_mask.value = '';
			route_gateway.value = '';
			if(route.length>j)
			{
				route_ip.value = route[j].ip;
				route_mask.value = route[j].mask;
				route_gateway.value = route[j].gateway;
			}
			j++;

		}
	}

	$('#'+control_id+'_adptersettings_div').find('input[name=gateway]').val(jsonobj.gateway[0]);

	var dns = jsonobj.dns;
	var obj = $('#'+control_id+'_adptersettings_div').find('.class_dns_div').find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		if(input_obj.name=='dns')
		{
			if (dns.length > i)
			{
				input_obj.value=dns[i];
			}
		}
	}
}

function SetAllAdpterInfo(jsonobj)
{
	if(jsonobj.r!=0)
	{
        $("#restoreVolFormDiv").dialog('close');
		openErrorDialog({title:'错误',html:jsonobj.e});
		return;
	}
	SetAdpterInfoForEdit(jsonobj.master_adpter,'master');
	SetAdpterInfoForEdit(jsonobj.standby_adpter,'standby');
	myDrawHotBackup();

}

function GetServerId(jsonstr,newTabIndex)
{
	$('.pe_mywaite').hide();
	if(jsonstr.r!=0)
	{
        $("#restoreVolFormDiv").dialog('close');
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	SetAciTreeValueRadio('Servers_Tree2',jsonstr.serverid,'peserverid',jsonstr.destserverid);
	$( "#tabs" ).tabs( "option", "active", newTabIndex);

}

function ShowDriverTree(jsonstr)
{
	var api = $('#drivers_Tree').aciTree('api');

	for(i=0;i<jsonstr.list.length;i++)
	{
		var Inode={
			id:'ui_'+i,
			label:jsonstr.list[i].hardware,
			icon:'adapter',
			inode: false,
			hardid:jsonstr.list[i].id,
			compatibleid:jsonstr.list[i].compatible,
			os:jsonstr.list[i].windows,
			open:false};

		api.append(null,{itemData:Inode,
			success: function(item, options) {
				var id=options.itemData.id;
				var hardid=options.itemData.hardid;
				var compatibleid=options.itemData.compatibleid;
				var os=options.itemData.os;
				SearchDriver(id,hardid,compatibleid,os);
			}
		});
	}
}

function ShowDriverList(jsonstr)
{
	RefreshAciTree("drivers_Tree",null,ShowDriverTree,jsonstr);
}

function checkagentorpedriver(jsonstr)
{
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	if(jsonstr.update==1)
	{
	    var strs = '发现{}个硬件未匹配到驱动'.replace('{}', jsonstr.list.length);
        change_to_waiting(4, strs);
		$('#errinfo2').html('');
		update_driver_info(false, '');
		ShowDriverList(jsonstr);
	}
	else
	{
        g_same = true;
        change_to_checked(3, '完全匹配');
	}
}

function CheackAgentOrPeDriver(t_id) {
    var params="serverid="+t_id;//pe ident
	var pointid=get_point_id();
	params+='&pointid='+pointid;
	myAjaxGet('../restore_handle/?a=checkpedriver',params,checkagentorpedriver);
}

function check_is_same(t_id) {
    var params="serverid="+t_id;//pe ident
	var pointid=get_point_id();
	params+='&pointid='+pointid;
	myAjaxGet('../restore_handle/?a=check_is_same_computer&need_check_local_db=1',params,check_is_same_call_back, t_id);
}

function change_to_checked(id, result) {
    if (has_set_interval){
        return;
    }
    has_set_interval=true;
    var obj = $('#step' + id);
    var childs = obj.children();
    $(childs[0]).attr('class','status_checked_icon');
    $(childs[1]).attr('class','');
    $(childs[2]).html(result);
    var time = 8;
    rt_t1 = setInterval(function () {
                if (time <= 0) {
                    go_to_next_tabs(id);
                    $(childs[2]).html(result);
                    return ;
                }
                var  msg = result + '({}秒后跳转下一个界面)';
                msg = msg.replace('{}',time);
                $(childs[2]).html(msg);
                time = time - 1;
            }, 1000);
}

function check_is_same_call_back(jsonstr, t_id) {
    if (jsonstr.r != 0){
        $("#restoreVolFormDiv").dialog('close');
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
    }

    if (jsonstr.restore_to_self){
        $('#step2').attr('data-restore-to-self', '1');
    }else{
        $('#step2').attr('data-restore-to-self', '0');
    }

    if (jsonstr.is_same ==0 ){
        change_to_checked(2, '同构');
        g_same = true;
        return;
    }
    else{
        change_to_waiting(3,'异构');
        CheackAgentOrPeDriver(t_id);
    }
}

function go_to_next_tabs(id) {

	if (src_is_linux()){
		$( "#tabs" ).tabs( "option", "active", 9 );
		clearInterval(rt_t1);
		return;
	}

    if (id==4){
        $( "#tabs" ).tabs( "option", "active", 7 );
    }
    else{
        $( "#tabs" ).tabs( "option", "active", 8 );
    }
    clearInterval(rt_t1);
}

function addRouteUI(id)
{
	var html = '<div style="border:1px solid #d3d3d3;border-top:0px;">';
	html += '<div style="padding-top:10px;">目标网络：<input type="text" name="route_ip"/></div>';
	html += '<div style="margin-top:10px;">子网掩码：<input type="text" name="route_mask"/></div>';
	html += '<div style="margin-top:10px;margin-bottom:10px;">网　　关：<input type="text" name="route_gate"/></div>';
	html += '</div>';
	$('#'+id).append(html);
}

$('#addRouteButton1').click(function(){
	addRouteUI('route_ui_1');

});


$('#addRouteButton2').click(function(){
	addRouteUI('route_ui_2');
});

$('#addRouteButton3').click(function(){
	addRouteUI('route_ui_3');
});

var g_maps = null;

function volRefreshSrcServer()
{
	var serverid = getaciTreeChecked('Servers_Tree2');
	var pointid = get_point_id();
	var parms = 'server_id=' + serverid + '&point_id=' + pointid;
	$('#msg_vol').text('获取磁盘信息中...');
    $('.mywaite_vol').show();
    $('#disk_vol_content ul').empty();
    g_maps = null;
	myAjaxGet('../restore_handle/?a=get_disk_vol_maps', parms, show_disk_vol);
}

function show_disk_vol(jsonstr) {
    $('.mywaite_vol').hide();
    if (jsonstr.r !=0){
        openErrorDialog('错误',jsonstr.e);
        $("#restoreVolFormDiv").dialog('close');
        return;
    }
    else{
        if (jsonstr.maps.length ==0){
           openErrorDialog('错误','获取卷对应关系失败！');
            $("#restoreVolFormDiv").dialog('close');
            return;
        }
        g_maps = jsonstr.maps;
        $.each(jsonstr.maps, function (index, item) {
            var line = $('<li></li>').css({'margin':'10px 5px 0 5px','padding':0,'list-style-type':'none'});
            var left_div = $('<div style="float:left;height:27px;line-height:27px;vertical-align:middle;width:45%;overflow:auto;"></div>');
            var right_div = $('<div style="float:right;height:27px;line-height:27px;vertical-align:middle;width:45%;"></div>');
            var check_box = $('<input type="checkbox"/>').attr('value', index);
            var img_contenter = $('<span></span>');
            var src_show_name = $('<span style="word-break:break-all"></span>').text(item.display_name).attr('title',item.display_name);
            if (item.target_vol.length){
                var select = $('<select style="width:100%;height:100%"></select>');
                $.each(item.target_vol, function (_index, _item) {
                    var name = _item.target_display_name;
                    var option = $('<option></option>').text(name).attr({'value': _index,'title':name});
                    select.append(option);
                });
                right_div.append(select);
            }
            else{
                right_div.append('无对应盘符。');
                check_box.attr('disabled',true);
            }
            if (!item.valid){
                check_box.attr('disabled',true);
            }
            var _msg = $('<div style="float:left;height:27px;line-height:27px;vertical-align:middle;width:9%;text-align:center; vertical-align:middle;">恢复到</div>');
            left_div.append(check_box).append(img_contenter).append(src_show_name);
            line.append(left_div).append(_msg).append(right_div);
            $('#disk_vol_content ul').append(line);
            $('#disk_vol_content ul').append('<div class="clear"></div>');
        })
    }
}

$('#driver_update_method input[value=2]').click(function () {

    var pointid=get_point_id();
	var destserverid=getaciTreeChecked('Servers_Tree2');
	var tmpid=GetAciTreeParent('Servers_Tree2',destserverid);
	if(tmpid=='ui_1')
	{
		destserverid=GetAciTreeValueRadio('Servers_Tree2',destserverid,'peserverid');
	}

	var params="destserverid="+destserverid;

	params+="&pointid="+pointid;
    $('#driver_version_tree').html('');
    RefreshAciTree("driver_version_tree",'../restore_handle/?a=get_driver_version&'+params, initCallback);
    $('#msg_driver_mode').text('获取驱动版本中');
    $('.mywaite_driver_mode').show();
});

function initCallback() {
    $('#choice2_warp').show();
}

$('#driver_update_method input[value=1]').click(function () {
    $('#choice2_warp').hide();
    $('.mywaite_driver_mode').hide();
});

function ShowDriverVersionTree(jsonstr)
{
	var api = $('#driver_version_tree').aciTree('api');

	for(i=0;i<jsonstr.list.length;i++)
	{

		var Inode={
			id:'ui_'+i,
			label:jsonstr.list[i].hardware,
			icon:'adapter',
			inode: false,
			hardid:jsonstr.list[i].id,
			compatibleid:jsonstr.list[i].compatible,
			os:jsonstr.list[i].windows,
			open:false};

		api.append(null,{itemData:Inode,
			success: function(item, options) {
				var id=options.itemData.id;
				var hardid=options.itemData.hardid;
				var compatibleid=options.itemData.compatibleid;
				var os=options.itemData.os;
				SearchDriver(id,hardid,compatibleid,os);
			}
		});
	}
}

function SearchDriver(node_id,hardids,compatibleids,os)
{
	var ids = hardids.concat(compatibleids);
	ids.sort(function(a,b){
            return a.length-b.length;
	});
	if( ids.length > 0 )
	{
		var p={
			org_ids:ids.concat(),
			node_id:node_id,
			ids:ids,
			os:os
		};
		var url =$('#aiourl').val()+'/index.php/api/drivers/?a=search&id='+encodeURIComponent(p.ids.pop())+'&os='+encodeURIComponent(p.os);
		myJsonp(url,'errinfo2',SearchDriverCallback,p,30*1000);
	}
}

function getInode(treeid,id)
{
	var api = $('#'+treeid).aciTree('api');

	var Inode = null;
	var children=api.children(null, true, true);
	children.each(api.proxy(function(element) {
		var item = $(element);
		var tmpid=this.getId(item);
		if(id==tmpid)
		{
			Inode=api.itemFrom(item);
			api.setInode(Inode,{inode:true});
			return false;
		}
	}, true));

	return Inode;
}

function NewItem(id,label,radio,checked)
{
	var itemData = {
		id:id,
		label:label,
		icon:'adapter',
		checkbox:radio,
		checked:checked,
		inode: false
	};
	return itemData;
}

function SearchDriverCallback(jsonstr,p)
{
    if (!has_set_interval){
        change_to_checked(4,'查询成功');
    }
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	var node_id=p.node_id;
	var Inode=getInode('drivers_Tree',node_id);
	if( Inode == null)
	{
		return;
	}
	var api = $('#drivers_Tree').aciTree('api');
	if( jsonstr.list.length > 0 )
	{
		for(var i=0;i<jsonstr.list.length;i++)
		{
			var id = jsonstr.list[i].id;
			var name = jsonstr.list[i].name+'('+jsonstr.id+')';
			var checked=false;
			if(i==0)
			{
				checked = true;
			}
			var newitem=NewItem(id,name,true,checked);
			api.append(Inode,{itemData:newitem,
					success: function() {
						this.open(Inode);
					}
				}
			);
		}
		return;
	}
	if( p.ids.length > 0 )
	{
		var url =$('#aiourl').val()+'/index.php/api/drivers/?a=search&id='+encodeURIComponent(p.ids.pop())+'&os='+encodeURIComponent(p.os);
		myJsonp(url,'errinfo2',SearchDriverCallback,p,30*1000);
	}
	else
	{
		//没有匹配成功
		update_driver_info(true, p.os);
		p.org_ids.sort(function(a,b){
            return b.length-a.length;
		});
		var label=api.getLabel(Inode);
		api.setLabel(Inode,{label: label+'<span style="color:red;">(未匹配)</span>'});
		for(var i=0;i<p.org_ids.length;i++)
		{
			var api = $('#drivers_Tree').aciTree('api');
			var id = p.org_ids[i];
			var name = p.org_ids[i];
			var checked=false;
			var newitem=NewItem(id,name,false,checked);
			api.append(Inode,{itemData:newitem,
					success: function() {
						//this.open(Inode);
					}
				}
			);
		}
	}
}

$('#driver_version_tree').on('acitree', function(event, api, item, eventName, options) {
	// get the item ID
	//console.log(eventName);
	if (eventName == 'beforeappend'){
		$('.mywaite_driver_mode').hide();
	}
	if (eventName == 'loadfail'){
		$('.mywaite_driver_mode').hide();
		if(check_multi_version){
            $('#driver_update_method input[value=1]').click();
            check_multi_version = false;
            return ;
        }
		$('#driver_version_tree').html('未获取到匹配的驱动版本。');
		$('#driver_version_tree').height('32px');
	}
	if (eventName === 'loaded'){
		uncheck_box_of_drivers_tree($('#driver_version_tree'));
		append_choice($('#driver_version_tree'));
		append_force_install_button($('#driver_version_tree'));

	}
});

$('#Servers_Tree2').on('acitree', function(event, api, item, eventName, options) {
	if (eventName == 'selected'){
		var itemData = api.itemData(item);
		var id=api.getId(item);
		UnCheckAllAciTreeRadios('Servers_Tree2',id);
	}
	if (eventName === 'added'){
	    var src_ident = getaciTreeChecked('Servers_Tree1');
	    if (src_ident == api.getId(item)){
	        api.setLabel(item, {'label':'<span style="color:blue;">[源机]</span>' + api.getLabel(item)})
        }
	}
});

$('div.auto_switch_div input[name=detection_type]').click(function(){
	myDrawHotBackup();
});

var driver_info = {'is_no_driver':false, 'os':''};
function update_driver_info(is_no_driver, os){
    driver_info = {'is_no_driver':is_no_driver, 'os':os};
    $('#os_info').text(os);
}

function is_no_driver() {
    return driver_info['is_no_driver']
}

function notify_no_driver() {
    var msg = '发现有未匹配的驱动，继续操作可能导致目标机无法启动。点击“确定”忽略此次警告，继续任务；点击“取消”关闭此对话框。';
    var msg1 = '请联系超级管理员下载操作系统：[' + '<span style="color:red">'+ driver_info['os'] +'</span>' + ']的驱动，更新至{{ title }}后，再次进行操作。';
    var p = $('<p style="text-indent:2em;margin-top:10px;"></p>').html(msg);
    var p1 = $('<p style="text-indent:2em;margin-top:10px;"></p>').html(msg1);
    var html = $('<div></div>').append(p).append(p1);
    openConfirmDialog({
        title: '警告',
        html: html,
        height: 300,
        width:600,
        onBeforeOK: function () {
            $(this).dialog('close');
            $("#tabs").tabs("option", "active", 8);
        },
        onCancel: function () {
            return;
        }
    });
}

function myupdate()
{
	var driverfileid=getAciTreeBoxChecked('drivers_Tree');
	var url = $('#aiourl').val()+'/index.php/api/drivers/?a=getdownload&ids='+driverfileid;
	$('#msg').html('正在生成驱动压缩包，这可能需要几分钟的时间。');
	$('.mywaite').show();
	myJsonp(url,'errinfo2',getdownloadCallback,null,10*60*1000);
}

function getdownloadCallback(jsonstr)
{
	$('.mywaite').hide();
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	var url = jsonstr.url;
	$('#imgurl').attr('href',url);
	$('#imgurl2').attr('href',url);
	var formid='downloadForm';
	var height = 320;
	if (!window.FileReader)
	{
		var head = document.getElementsByTagName('head')[0];
		var link = document.createElement('link');
		link.href = '/static/js/uploadify/uploadify.css';
		link.rel = 'stylesheet';
		link.type = 'text/css';
		head.appendChild(link);

		formid='downloadFormFlash';
		height = 250;
		$.getScript('/static/js/uploadify/jquery.uploadify.min.js',function(){
			$('#localfileflash').uploadify({
				'formData'     : {
					'csrfmiddlewaretoken' : $.cookie('csrftoken')
				},
				'swf'      : '/static/js/uploadify/uploadify.swf',
				'uploader' : '../version_handle/?a=uploadbyflash',
				'multi'    : false,
				'auto': true,
				'buttonText': '选择并上传镜像文件'
			});
		});
	}
	$("#"+formid).attr('title','更新').dialog({
		autoOpen: true,
		height: height,
		width: 420,
		modal: true,
		close: function(){
		}
	});
}

function disable_all_input(adpter_settings_div,disabled)
{
	var obj = $('#'+adpter_settings_div).find('input');
	for(var i=0;i<obj.length;i++)
	{
		var input_obj = obj[i];
		$(input_obj).prop('disabled',disabled);
	}

	var obj = $('#'+adpter_settings_div).find('select');
	for(var i=0;i<obj.length;i++)
	{
		var select_obj = obj[i];
		$(select_obj).prop('disabled',disabled);
	}

}

function getswitchtype_input_name()
{
	var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
	if(switch_change_master_ip==1)
	{
		return 'ip_switch_tabs_1_switchtype';
	}
	return 'ip_switch_tabs_0_switchtype';
}

function get_ip_switch_tabs_div_id()
{
	var switch_change_master_ip = $("input[name='switch_change_master_ip']:checked").val();
	if(switch_change_master_ip==1)
	{
		return 'ip_switch_tabs_1';
	}
	return 'ip_switch_tabs_0';
}