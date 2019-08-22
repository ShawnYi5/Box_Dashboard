var g_op_type='create_plan';

function get_remote_aio_login_params()
{
	var remote_ip = $('#remote_ip').val();
	var remote_username = $('#remote_username').val();
	var remote_password = new Base64().encode($('#remote_password').val());
	var url = 'ip='+remote_ip;
	url += '&u='+remote_username;
	url += '&p='+remote_password;
	url += '&ssl='+(($('#tabs-0').find('input#enable-https').prop('checked'))?('1'):('0'));
	return url;
}

function Getstoragedevice(retjson)
{
	$("#storagedevice").empty();
	$.each(retjson, function(i, item){
		var free_GB = (item.free / Math.pow(1024, 1)).toFixed(2);
        free_GB = (item.value == -1) ? 0 : free_GB;

		var html="（可用：{3}）";
		html = html.replace('{3}', free_GB+'GB');

		if($('#sel_storagedevice').val()==item.value)
		{
			$("#storagedevice").append("<option value='"+item.value+"'  selected=\"selected\" >"+item.name+"</option>");
		}
		else
		{
			$("#storagedevice").append("<option value='"+item.value+"'>"+item.name+html+"</option>");
		}
	});
	if(isInEditRemotePlan()){
		$("#storagedevice").val(window.remote_plan_ext_config['storage_device']['value']);
	}
}

function createremotebackupcallback(jsonobj)
{
	if (jsonobj.r != 0)
	{
        openErrorDialog('错误',jsonobj.e);
        unlock_btns();
		return;
    }
    var html='新建远程容灾计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况。';
	if(isInEditRemotePlan()){
		html='更改计划成功。';
		$("#newremotebackupFormDiv").dialog("close");
	}

	openSuccessDialog({title:'完成',html:html});
	activeOneTab(0);

}

function Onfinish()
{
	var params="a=createremotebackup";
	params += '&'+get_remote_aio_login_params();
	params += '&name='+encodeURIComponent($('#planname').val());
	params += '&ident='+getaciTreeChecked('Servers_Tree1');
	params += '&display_name='+encodeURIComponent(getaciTreeNameChecked('Servers_Tree1'));
	params += '&network_transmission_type='+$('#network_transmission_type').val();
	params += '&storage_node_ident='+$("#storagedevice").val();
	params += '&full_param='+get_full_params(true);
	params += '&edit_plan_id='+fetch_editing_plan_id();
	lock_btns();
	myAjaxPost('../remotebackup_handle/',params,createremotebackupcallback);
}

function lock_btns() {
	$('#prev').attr('disabled', true);
	$('#next').attr('disabled', true);
}

function unlock_btns() {
	$('#prev').attr('disabled', false);
	$('#next').attr('disabled', false);
}

var firstIndex = 0, lastIndex = 4;

function activeOneTab(tab2show) {
	if (tab2show === firstIndex) {
		$('#prev').hide();
	}
	else {
		$('#prev').show();
	}

	if (tab2show === lastIndex) {
		$('#next').val('完成');
	}
	else {
		$('#next').val('下一步»');
	}

	$("#tabs").tabs('option', 'active', tab2show);
	unlock_btns();
}

$('#next').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');
	if (!check_input_is_valid()){
		return;
	}

	if(selindex===0) {
		$('.mywaite').show();
		var params="a=test_access_to_remote_aio&"+get_remote_aio_login_params();
		myAjaxGet('../remotebackup_handle/',params,function (jsdata) {			// 测试是否A机可正常访问
			$('.mywaite').hide();
			if(jsdata.access_able){			// 访问A机正常
				if(isInEditRemotePlan()){
					RefreshAciTree("Servers_Tree1", null, SetRemoteHost, window.remote_plan_ext_config.sync_host);
				}
				else {
					RefreshAciTree("Servers_Tree1", '../remotebackup_handle/?a=getlist&'+get_remote_aio_login_params()+'&id=');
				}
				activeOneTab(1);
			}
			else {							// 访问A机异常
				openErrorDialog('错误', '连接远端灾备系统失败，请检查 "IP 账号 密码" 是否正确，网络是否异常。');
			}
		});
		return false;
	}
	else if(selindex===1) {
		setScheduleCycle();
		setStorageDevice();
		show_or_hide_schedule_type_choices_when_editing();
	}
	else if(selindex===2) {
		set_sundry_options();
	}
	else if(selindex===3){
		var full_param = get_full_params(false);
		var tab$ = $('#tabs-4');
		showPlanDetialTable(tab$, full_param);
	}
	else if(selindex===4){
		Onfinish();
	}

	if(selindex + 1 <= lastIndex){
		activeOneTab(selindex + 1);
	}
});

function setStorageDevice() {
	myAjaxGet('../backup_handle/','a=getstoragedevice',Getstoragedevice);
}

function show_or_hide_schedule_type_choices_when_editing() {
	$('input[type=radio][value=bak-continue]').parent().show();
	$('input[type=radio][value=bak-cycled]').parent().show();
	$('input[type=radio][value=bak-perweek]').parent().show();
	$('input[type=radio][value=bak-permonth]').parent().show();

	if(isInEditRemotePlan()){
		var backup_period =  window.remote_plan_ext_config['backup_period'];
		var period_type = backup_period['period_type'];
		if(period_type === 'bak-continue'){
			$('input[type=radio][value=bak-cycled]').parent().hide();
			$('input[type=radio][value=bak-perweek]').parent().hide();
			$('input[type=radio][value=bak-permonth]').parent().hide();
		}
		else {
			$('input[type=radio][value=bak-continue]').parent().hide();
		}
	}
}

function setScheduleCycle() {
	clearSelectedDays();
	$('#tabs-2 .start-time').val(nowTimeStr());
	var tab2 = $('#tabs-2');

	if(isInEditRemotePlan()){
	   	var backup_period =  window.remote_plan_ext_config['backup_period'];
		var period_type = backup_period['period_type'];
		var parent = tab2.find('input[value=what]'.replace('what', period_type)).parent();

		tab2.find('input[value=what]'.replace('what', period_type)).trigger('click');
		parent.find('.start-time').val(backup_period['start_datetime']);
		if (period_type === 'bak-continue') {
		}
		if (period_type === 'bak-cycled') {
			parent.find('input[type=number]').val(backup_period['addition']);
			parent.find('select[id=interval-unit]').val(backup_period['val_unit']);
		}
		if (period_type === 'bak-perweek') {
			parent.find('input[name=weeks]').prop('checked', false);
			var weeks = backup_period['addition'].split(',');
			$.each(parent.find('input[name=weeks]'), function (i, week) {
				if ($.inArray($(week).val(), weeks) > -1) {
					$(week).prop('checked', true);
				}
			})
		}
		if (period_type === 'bak-permonth') {
			parent.find('#dayselect').find('div').removeClass('myonedayselected');
			var days = backup_period['addition'].split(',');
			$.each(parent.find('#dayselect').find('div'), function (i, day) {
				if ($.inArray($(day).text(), days) > -1) {
					$(day).addClass('myonedayselected');
				}
			})
		}
	}
	else {
		tab2.find('input[value=bak-continue]').trigger('click');
	}
}

function set_sundry_options() {
	show_or_hide_uis();
	var tab3 = $('#tabs-3');
	if(isInEditRemotePlan()){
		var fullParam = window.remote_plan_ext_config;
		   if(fullParam.data_keep_duration){
		   		get_set_data_keep_duration(fullParam.data_keep_duration.value, fullParam.data_keep_duration.unit);
			}
			else {
				get_set_data_keep_duration(fullParam.data_keep_months.value, 'month');
			}
			tab3.find('#keepingpoint').val(fullParam.mini_keep_points.value);
			tab3.find('#cleandata').val(fullParam.space_keep_GB.value);
			tab3.find('#continuous-sync').val(fullParam.continue_windows.value);
			if (fullParam.max_network_Mb.value !== '-1'){
				tab3.find('#usemaxbandwidth').prop('checked', true);
				tab3.find('#maxbandwidth').prop('disabled', false);
				tab3.find('#maxbandwidth').val(fullParam.max_network_Mb.value);
			}
			else {
				tab3.find('#usemaxbandwidth').prop('checked', false);
				tab3.find('#maxbandwidth').prop('disabled', true);
				tab3.find('#maxbandwidth').val('1000');
			}
			tab3.find('#sync-encryption').val(fullParam.transfer_encipher.value);
			if(!fullParam.retry_setting.value || fullParam.retry_setting.value === '-1|-1'){
				tab3.find('#enable-backup-retry').prop('checked', false);
				tab3.find('#retry-counts').prop('disabled', true);
        		tab3.find('#retry-interval').prop('disabled', true);
        		tab3.find('#retry-counts').val('5');
        		tab3.find('#retry-interval').val('10');
			}
			else {
				tab3.find('#enable-backup-retry').prop('checked', true);
				tab3.find('#retry-counts').prop('disabled', false);
        		tab3.find('#retry-interval').prop('disabled', false);
				tab3.find('#retry-counts').val(fullParam.retry_setting.value.split('|')[0]);
        		tab3.find('#retry-interval').val(fullParam.retry_setting.value.split('|')[1]);
			}
	}
	else {
		get_set_data_keep_duration('1', 'month');
		tab3.find('#keepingpoint').val('5');
		tab3.find('#cleandata').val('200');
		tab3.find('#continuous-sync').val('2');
		tab3.find('#usemaxbandwidth').prop('checked', false);
		tab3.find('#maxbandwidth').prop('disabled', true);
		tab3.find('#maxbandwidth').val('1000');
		tab3.find('#sync-encryption').val('no');
		tab3.find('#enable-backup-retry').prop('checked', true);
		tab3.find('#retry-counts').prop('disabled', false);
		tab3.find('#retry-interval').prop('disabled', false);
		tab3.find('#retry-counts').val('5');
		tab3.find('#retry-interval').val('10');
	}
}

function SetRemoteHost(host) {
	var api = $('#Servers_Tree1').aciTree('api');
	var Inode = {
		id: host.value,
		label: host.label,
		icon: 'pc',
		inode: false,
		radio: true,
		checked: true,
		open: false
	};
	api.append(null, {
		itemData: Inode,
		success: function (item, options) {
			$("#Servers_Tree1 .aciTreeItem").first().click();
		}
	});
}

function check_input_is_valid() {
	var selindex=$("#tabs").tabs('option', 'active');
	var tab0=$('#tabs-0'), tab1=$('#tabs-1'), tab2=$('#tabs-2'), tab3=$('#tabs-3');

	if (selindex === 0){
		if ($('#planname').val() == ''){
			openErrorDialog('错误', '输入无效，请输入计划名称。');
			return false;
		}
		if (!isIPV4($('#remote_ip').val())){
			openErrorDialog('错误', '输入无效，请输入合法的ip地址。');
			return false;
		}
		if ($('#remote_ip').val() == window.location.host){
			openErrorDialog('错误', '输入无效，请输入其它智动全景灾备系统ip地址。');
			return false;
		}
		if ($('#remote_username').val() == ''){
			openErrorDialog('错误', '输入无效，请输入远端系统管理员账号。');
			return false;
		}
		if ($('#remote_username').val() == 'admin'){
			openErrorDialog('错误', '输入无效，请输入非admin用户。');
			return false;
		}
		if ($('#remote_password').val() == ''){
			openErrorDialog('错误', '输入无效，请输入远端系统管理员密码。');
			return false;
		}
	}
	else if(selindex===1){
		var srcHost = getSrcHost(tab1);
		if(srcHost['value'] === ''){
			openErrorDialog('错误', '请选择远程容灾源客户端。');
			return false;
		}
	}
	else if(selindex===2){
		var sche_checked =  getBackupPeriod(tab2);

		if (getSelectedStorage(tab2)['value'] === '-1') {
			openErrorDialog({title: '错误', html: '请选择存储设备。'});
			return false;
		}

		if(sche_checked['start_datetime'] === ''){
			openErrorDialog('错误', '输入无效，请选择开始时间。');
			return false;
		}

		if(sche_checked['period_type'] === 'bak-cycled'){
			if(!isNum(sche_checked['addition']) || parseInt(sche_checked['addition']) === 0) {
				openErrorDialog({title: '错误', html: '间隔时间数值输入无效，请重新输入。'});
				return false;
			}
			if(parseInt(sche_checked['addition']) > 1000) {
				openErrorDialog({title: '错误', html: '间隔时间数值超过1000，请重新输入。'});
				return false;
			}
		}
		else if(sche_checked['period_type'] === 'bak-perweek'){
			if (sche_checked['addition'] === '') {
				openErrorDialog({title: '错误', html: '请选择每周同步星期。'});
				return false;
			}
		}else if(sche_checked['period_type'] === 'bak-permonth'){
			if (sche_checked['addition'] === '') {
				openErrorDialog({title: '错误', html: '请选择每月同步日期。'});
				return false;
			}
		}
	}
	else if(selindex===3){
		var fP = get_full_params(false);
		var isLimitNetSpeed = tab3.find('#usemaxbandwidth').prop('checked');
		var isRetry = tab3.find('#enable-backup-retry').prop('checked');
		var deadline = fP['data_keep_duration']['value'];
		var unit = fP['data_keep_duration']['unit'];

		if(unit === 'day' && !is_positive_num_and_in_range(deadline, '3', '360')){			// todo 文字调整
			openErrorDialog({title:'错误',html:'**备份数据保留期**  \n输入无效，请重新输入'});
			return false;
		}
		if(unit === 'month' && !is_positive_num_and_in_range(deadline, '1', '240')){
			openErrorDialog({title:'错误',html:'**备份数据保留期**  \n输入无效，请重新输入'});
			return false;
		}

		if(!is_positive_num_and_in_range($('#keepingpoint').val(), '1', '999')){
			openErrorDialog({title:'错误',html:'**至少保留备份点数**  \n输入无效，请重新输入'});
			return false;
		}

		if(!is_positive_num_and_gte($("#cleandata").val(), 100)){
			openErrorDialog({title:'错误',html:'**本用户存储空间配额低于**  \n输入无效，请重新输入'});
			return false;
		}

		if (isLimitNetSpeed) {
			if (!isNum(fP['max_network_Mb']['value']) || parseInt(fP['max_network_Mb']['value']) === 0) {
				openErrorDialog({title:'错误',html:'**最大网络带宽**  \n输入无效，请重新输入'});
				return false;
			}
		}

		if (is_norm_type_plan() && isRetry){
			var cnt_intv = fP['retry_setting']['value'].split('|');
			var retry_cnt=cnt_intv[0], retry_intv=cnt_intv[1];
			if(!isNum(retry_cnt) || parseInt(retry_cnt) === 0){
				openErrorDialog({title:'错误',html:'**重试次数**  \n输入无效，请重新输入'});
				return false;
			}
			if(!isNum(retry_intv) || parseInt(retry_intv) < 5){
				openErrorDialog({title:'错误',html:'**重试间隔**  \n输入无效，请重新输入'});
				return false;
			}
		}
	}

	return true;
}

$('#prev').click(function() {
	var curIndex=$("#tabs").tabs('option', 'active');
	if (curIndex - 1 >= firstIndex) {
		activeOneTab(curIndex - 1);
	}
});

$('#RefreshSrcServer').button().click(function(){
	var url = '../remotebackup_handle/?a=getlist';
	url += '&'+get_remote_aio_login_params();
	url += '&id=';
	RefreshAciTree("Servers_Tree1",url);
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
	$('#show_total').html(jsonstr.total ? jsonstr.total + 'GB' : '');
	$('#show_use').html(jsonstr.use ? jsonstr.use + 'GB' : '');
	$('#network_transmission_type').val(jsonstr.network_transmission_type);

	var host_ext_info = JSON.parse(jsonstr.host_ext_info);
	if(host_ext_info.nas_path)
	{
		$('#nas_table_info').show();
		$('#normal_table_info').hide();
		$('div#show_nas_servername').html(jsonstr.servername);
		$('div#show_nas_path').html(host_ext_info.nas_path);
		if(isNFSpath(host_ext_info.nas_path))
		{
			$('div#show_nas_protocol').html('NFS');
		}
		else
		{
			$('div#show_nas_protocol').html('CIFS');
		}
	}
	else
	{
		$('#nas_table_info').hide();
		$('#normal_table_info').show();
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
		params += '&'+get_remote_aio_login_params();
		myAjaxGet('../remotebackup_handle/',params,GetServerInfoCallback);
	}
});

function GetBackupPoint(jsonstr,newTabIndex)
{

	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	if(jsonstr.list.length>0)
	{
		$('#pointid').val('nonone');
		$("#tabs").tabs( "option", "active", newTabIndex);
		return;
	}
	else
	{
		var msg = '<p style="margin: 0;text-indent: 2em">客户端创建远程灾备计划需要至少有一个已经备份完成的可使用备份点。</p>\
                    <p style="margin: 0;text-indent: 2em">此客户端没有备份点或备份点正在备份中，无可使用备份点。</p>\
                    <p style="margin: 0;text-indent: 2em">请先为此客户端创建备份计划，并等待备份完成后再创建远程灾备计划。 </p>'
		openErrorDialog({title:'错误',html:msg, width: 450, height: 250});
		return;
	}
}

$('#tabs').on('tabsbeforeactivate', function (event, ui) {
	var oldTabIndex = ui.oldTab.index();
	var newTabIndex = ui.newTab.index();
	if( g_op_type=='create_plan' && oldTabIndex == 1 && newTabIndex == 2 )
	{
		var pointid = $('#pointid').val();
		if( pointid == 'none')
		{
			var params = 'host_ident='+getaciTreeChecked('Servers_Tree1');
			//params += '&checkident=1';
			params += '&'+get_remote_aio_login_params();
			myAjaxGet('../remotebackup_handle/?a=get_point_list',params,GetBackupPoint,newTabIndex);
			return false;
		}
	}
});

function isInEditRemotePlan() {
	try{
		return ($("#newremotebackupFormDiv").dialog("isOpen") && window.edit_remote_plan_ing === 'yes');
	}
	catch (err){
		return false;
	}
}

function show_or_hide_refresh_butt(show) {
	if (show) {
		$('#RefreshSrcServer').show();
	}
	else {
		$('#RefreshSrcServer').hide();
	}
}

function show_or_hide_search_butt(show) {
	if (show) {
		$('input[name=client_search]').show();
	}
	else {
		$('input[name=client_search]').hide();
	}
}

// '1,2,3,4,5'、'1'、null
function getSelectedIds(Cnt) {
	var ids = $('#list').jqGrid('getGridParam', 'selarrrow');
	if (Cnt === 'one' && ids.length !== 1) {
		openErrorDialog({title: '错误', html: '请选择一条数据。'});
		return null;
	}
	if (Cnt === 'many' && ids.length === 0) {
		openErrorDialog({title: '错误', html: '请至少选择一条数据。'});
		return null;
	}
	return ids.join(',');
}

function nowTimeStr() {
	return moment().format('YYYY-MM-DD HH:mm:ss');
}

function init_tab0_when_create_plan() {
    $('#planname').val('远程灾备计划 '+nowTimeStr());
    $('#remote_ip').val('');
    $('#remote_username').val('');
    $('#remote_password').val('');
    $('#tabs-0').find('input#enable-https').prop('checked', true);
}

function init_tab0_when_edit_plan() {
	var ext_config = window.remote_plan_ext_config;
	$('#remote_ip').val(ext_config['remote_aio']['aio_ip']);
	$('#remote_username').val(ext_config['remote_aio']['username']);
	$('#remote_password').val(ext_config['remote_aio']['password']);
	$('#planname').val(ext_config['plan_name']['label']);
	$('#tabs-0').find('input#enable-https').prop('checked', ext_config['enable_https']['value']);
}

function fetch_editing_plan_id() {
	if(isInEditRemotePlan()){
		var select_plans = $('#list').jqGrid('getGridParam', 'selarrrow');
		return select_plans[0];
	}
	else {
		return '';
	}
}

// unit: 'day', 'month'
function get_set_data_keep_duration(val, unit) {
	if(val && unit){
		$('#retentionperiod').val(val);
		$('#retentionperiod-unit').val(unit);
	}
	else {
		return {'val':$('#retentionperiod').val(), 'unit': $('#retentionperiod-unit').val()};
	}
	set_retentionperiod_msg();
}

function set_retentionperiod_msg() {
	if($('#retentionperiod-unit').val() === 'day'){
		$('#retentionperiod-msg').text('3-360天');
	}
	else {
		$('#retentionperiod-msg').text('1-240月');
	}
}

$('#retentionperiod-unit').on('change', function () {
	set_retentionperiod_msg();
});