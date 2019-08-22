function CreateBackupCallback(jsonstr,immediately)
{
	var curPage = '{{ page }}';
	if(jsonstr.r!=0)
	{
		try
		{
			var new_url = '../backupmgr_handle/?group=group&a=list';
			if(curPage == 'taskpolicy')
			{
				new_url='../backup_handle/?a=policylist';
			}
			$('#list').setGridParam({url:new_url});
			$('#list').trigger("reloadGrid",[{page:1}]);
		}
		catch (e)
		{
		}
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	InitBackupControlData();
	try
	{
		$("#newbackupFormDiv").dialog('close');
	}
	catch (e)
	{
	}
	try
	{
		var new_url = '../backupmgr_handle/?group=group&a=list';
		if(curPage == 'taskpolicy')
		{
			new_url='../backup_handle/?a=policylist';
		}
		$('#list').setGridParam({url:new_url});
		$('#list').trigger("reloadGrid",[{page:1}]);
	}
	catch (e)
	{
	}

	if(curPage == 'createbackup'){
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 备份源</div>');
		var html='新建备份计划成功，您可在<a href="../mgrbackup" style="color:blue;">备份计划管理</a>中查看已创建的计划任务。';
		if(immediately==1)
		{
			var html='新建备份计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况，或在<a href="../mgrbackup" style="color:blue;">备份计划管理</a>中管理已创建的备份计划。';
		}
		openSuccessDialog({title:'完成',html:html});
	}
	else if(curPage == 'mgrbackup'){
		$('#navigation').html('<div class="font_navigation">备份计划管理</div>');
		if(immediately==1)
		{
			var html='新建备份计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况。';
			openSuccessDialog({title:'完成',html:html});
		}

	}
	else if(curPage == 'taskpolicy'){
		$('#navigation').html('<div class="font_navigation">备份计划策略</div>');
	}
	else {
		$('#navigation').html('<div class="font_navigation"></div>');
	}
}

function hideBackupControl()
{
	$('#RefreshSrcServer').hide();
	$("input[name=bakschedule]").prop("disabled",false);
}

function showBackupControl()
{
	$('#RefreshSrcServer').show();
	$("input[name=bakschedule]").prop("disabled",false);
}

function InitBackupControlData()
{
	$( "#tabs" ).tabs( "option", "active", 0 );
	$('#taskid').val('');
	$("#sel_storagedevice").val('');
	if(!isInEdit()){
		$('#taskname').val('');
	}
	$('#stime0').val() || $('#stime0').val(curTime);
	$('#stime1').val() || $('#stime1').val(curTime);
	$('#stime2').val() || $('#stime2').val(curTime);
	$('#stime3').val() || $('#stime3').val(curTime);
	$('#stime4').val() || $('#stime4').val(curTime);
	$("div.myoneday").removeClass("myonedayselected");
	$(".prev").hide();
	$('#next').attr("value","下一步»");
	$("input[name=bakschedule][value=1]").prop("checked",true);
	$("input[name=perweek]").prop("checked",false);
	$('#bakschedule2').hide();
	$('#bakschedule3').hide();
	$('#bakschedule4').hide();
	$('#bakschedule5').hide();
	$('#usemaxbandwidth').prop('checked',true);
	$('#maxbandwidth').val(300);

	$('#enable-backup-retry').prop('checked',true);
	$('#retry-counts').val(5);
	$('#retry-interval').val(3);

	init_shell_infos_from_ui($('#tabs-shell'));
}

function verify_interval_day_hour_min(interval) {
	interval = parseInt(interval);
	var unit = $('#interval-unit').find(':selected').val();
	if(unit === 'day'){
		if(interval > 365 || interval < 1){
			return '请输入正确的间隔天数(1-365)'
		}
	}
	else if (unit === 'hour'){
		if(interval < 1){
			return '请输入正确的间隔小时(最小1小时)'
		}
	}
	else if (unit === 'min'){
        if(interval < 5){
			return '请输入正确的间隔分钟(最小5分钟)'
		}
	}
	else {
		return '单位错误: ' + unit
	}

	return 'ok';
}

// 点击下一步时， 校验数据
function CheckData(selindex)
{
	if(selindex==1)
	{
		var taskname=$('#taskname').val();
		if(taskname=='')
		{
			var tmphtml = '计划名称不能为空。';
			if( $('#uitasktype').val() == 'uitaskpolicy' )
			{
				tmphtml = '策略名称不能为空。';
			}
			openErrorDialog({title:'错误',html:tmphtml});
			return false;
		}
		if( $(".storagedevicediv").is(":visible") && $("#storagedevice").val() == null  )
		{
			openErrorDialog({title:'错误',html:'选择备份存储设备。'});
			return false;
		}
		if( $(".storagedevicediv").is(":visible") && $("#storagedevice").val() == -1  )
		{
			openErrorDialog({title:'错误',html:'请先分配存储设备。'});
			return false;
		}
	}
	else if(selindex==0)
	{
		var serverid=getaciTreeChecked('Servers_Tree1');

		if(serverid=='')
		{
			openErrorDialog({title:'错误',html:'请选择备份源客户端。'});
			return false;
		}
	}
	else if(selindex==2)
	{
		var taskname=$('#taskname').val();
		if( $('#uitasktype').val() == 'uitaskpolicy' && taskname == '')
		{
			tmphtml = '策略名称不能为空。';
			openErrorDialog({title:'错误',html:tmphtml});
			return false;
		}
		if($('#uitasktype').val() == 'uitaskpolicy' && taskname.length >= 50)
		{
			tmphtml = '策略名称长度不能超过50个字符。';
			openErrorDialog({title:'错误',html:tmphtml});
			return false;
		}
		var schedule="";
		var backupschedule=$("input[name='bakschedule']:checked").val();
		$('#confirm_cdpperiod').html('-');
		$('#confirm_cdptype').html('-');
		if(backupschedule==1) // CDP计划
		{
			if($("#stime0").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}
		}
		else if(backupschedule==2)
		{
			if($("#stime1").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}
		}
		else if(backupschedule==3)
		{
			if($("#stime2").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}
			if($("#timeinterval").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择间隔时间。'});
				return false;
			}
			if(!isNum($("#timeinterval").val()))
			{
				openErrorDialog({title:'错误',html:'请输入正确的间隔时间, 正整数。'});
				return false;
			}

			var msg = verify_interval_day_hour_min($("#timeinterval").val());
			if(msg !== 'ok'){
				openErrorDialog({title:'错误', html:msg});
				return false;
			}
		}
		else if(backupschedule==4)
		{
			if($("#stime3").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}

			if($("input[name='perweek']:checked").length==0)
			{
				openErrorDialog({title:'错误',html:'请勾选每周几备份。'});
				return false;
			}

		}
		else if(backupschedule==5)
		{
			if($("#stime4").val()=='')
			{
				openErrorDialog({title:'错误',html:'请选择开始时间。'});
				return false;
			}
			if($("div.myoneday.myonedayselected").length==0)
			{
				openErrorDialog({title:'错误',html:'请勾选每月第几日备份。'});
				return false;
			}
		}
		if(backupschedule==1){						// 表示CDP计划
			$('.manage-item-4cdp').show();			// 空间清理设置：显示CDP项
			$('#full-back-options').hide();			// 高级设置1：隐藏是否完整备份
		}
		else {										// 表示普通计划
			$('.manage-item-4cdp').hide();			// 空间清理设置：隐藏CDP项
			$('#full-back-options').show();			// 高级设置1：显示是否完整备份
		}
		$('#advanced1-manage').hide();				// 初始，不可见
        $('#advanced2-manage').hide();
        $('#advanced3-manage').hide();
        $('#advanced4-manage').hide();
        $('#advanced1-manage_tt1').hide();
		$('#advanced1-lab').text('▶');
        $('#advanced2-lab').text('▶');
        $('#advanced3-lab').text('▶');
        $('#advanced4-lab').text('▶');

	}
	else if(selindex==3)  // 校验参数
	{
		$('#confirm_schedule').html(schedule);
		var retentionperiod=$("#retentionperiod").val() + '个月';
		$('#confirm_retentionperiod').html(retentionperiod);

		var cleandata= $("#cleandata").val() + 'GB';
		$('#confirm_cleandata').html(cleandata);

		var maxbandwidth= $("#maxbandwidth").val();
		var usemaxbandwidth = 1;
		if( $('#usemaxbandwidth').prop("checked") )
		{
			if(maxbandwidth<8 && maxbandwidth !=-1)
			{
				openErrorDialog({title:'错误',html:'备份时限定最大占用源主机的网络带宽至少为8Mbit/s。'});
				return false;
			}
		}

		var BackupIOPercentage=$("#BackupIOPercentage").val();
		if (!isNum(BackupIOPercentage)){
			openErrorDialog({title:'错误',html:'备份时限定最大占用源主机的存储性能的百分比必须是整数'});
			return false;
		}

		if (BackupIOPercentage <1 || BackupIOPercentage > 100){
			openErrorDialog({title:'错误',html:'备份时限定最大占用源主机的存储性能的百分比为[1,100]间的整数'});
			return false;
		}

		if( $('#enable-backup-retry').prop("checked") )
		{
			var count = $('#retry-counts').val(),
				interval = $('#retry-interval').val();
			if(count<1 || isNaN(count))
			{
				openErrorDialog({title:'错误',html:'重试次数需要大于零。'});
				return false;
			}
			if(interval<2 || isNaN(interval))
			{
				openErrorDialog({title:'错误',html:'重试间隔至少要2分钟。'});
				return false;
			}
		}

		if( !is_positive_num_and_in_range(set_get_thread_count(), '2', '8')  ){
			openErrorDialog({title:'错误',html:'**备份源存储读取队列深度**  \n输入无效, 请重新输入'});
			return false;
		}
		var deadline = get_data_keeps_deadline();
		var unit = get_data_keeps_deadline_unit();
		if(unit === 'day'){
			if(parseInt($("#tabs-3 .radio:checked").val()) == 1){
				var retentionperiod = parseInt($("#retentionperiod").val());
				var cdpperiod = parseInt($("#cdpperiod").val());
				var is_validity = retentionperiod >= (cdpperiod+3);
				console.log('deadline:',deadline,'is_validity:',is_validity);
				if(!is_positive_num_and_in_range(deadline, '5', '300') || !is_validity){
					var validity_day = cdpperiod+3 ;
					openErrorDialog({title: '错误', html: '**无效的“备份数据保留期”**  \n备份数据保留期需要大于等于'+validity_day+'天'});
					return false;
				}
			}else if(!is_positive_num_and_in_range(deadline, '3', '360')){
				openErrorDialog({title: '错误', html: '**无效的“备份数据保留期”**  \n输入无效，请重新输入'});
				return false;
			}
		}
		if(unit === 'month' && !is_positive_num_and_in_range(deadline, '1', '240')){
			openErrorDialog({title:'错误',html:'**无效的“备份数据保留期”**  \n输入无效，请重新输入'});
			return false;
		}

		if(!is_positive_num_and_in_range($('#keepingpoint').val(), '1', '999')){
			openErrorDialog({title:'错误',html:'**无效的“备份数据保留期”**  \n输入无效，请重新输入'});
			return false;
		}

		if(!is_positive_num_and_gte($("#cleandata").val(), 100)){
			openErrorDialog({title:'错误',html:'**本用户存储空间配额低于**  \n输入无效，请重新输入'});
			return false;
		}
	}
	else if(selindex==4) // 校验执行脚本
	{
		var tabsShell$ = $('#tabs-shell');
		var shell_infos = get_shell_infos_from_ui(tabsShell$);

		// 启用脚本功能
		if(is_enable_shell(tabsShell$)){
			if(shell_infos.exe_name === ''){
				openErrorDialog({title:'错误', html:'可执行文件名: 不能为空'});
				return false;
			}
			if(shell_infos.work_path === ''){
				openErrorDialog({title:'错误', html:'执行路径: 不能为空'});
				return false;
			}
			if(shell_infos.unzip_path === ''){
				openErrorDialog({title:'错误', html:'解压路径: 不能为空'});
				return false;
			}

			// 更改流程, 没有上传过文件, 当前未上传脚本
			if(isInEdit() && !is_exist_last_zip_in_aio(tabsShell$)){
				if(shell_infos.zip_file === ''){
					openErrorDialog({title:'错误', html:'.zip/.tar.gz: 请上传文件'});
					return false;
				}
			}

			// 创建流程, 当前未上传脚本
			if(!isInEdit()){
				if(shell_infos.zip_file === ''){
					openErrorDialog({title:'错误', html:'.zip/.tar.gz: 不能为空'});
					return false;
				}
			}
		}
	}
	return true;
}

// 创建计划完成，并提交参数到后台
function OnFinish(other_params)
{
	var tasktype = 'backup';
	if( $('#uitasktype').val() == 'uitaskpolicy' )
	{
		tasktype = 'policy';
	}
	var postdata='taskname='+encodeURIComponent($("#taskname").val());
	var backuptype=1;
	postdata+='&backuptype='+backuptype;
	postdata+='&serverid='+getaciTreeChecked('Servers_Tree1');
	postdata+='&storagedevice='+$("#storagedevice").val();
	var backupschedule=$("input[name='bakschedule']:checked").val();
	postdata+='&bakschedule='+backupschedule;
	var backupmode = $('#full-bak-everytime').prop("checked") ? '1' : '2';
	if(backupschedule==1)
	{
		//CDP
		postdata+='&cdpperiod='+$("#cdpperiod").val();
		postdata+='&cdptype='+$("input[name='cdptype']:checked").val();
		postdata+='&starttime='+$("#stime0").val();
		backupmode = 2;
	}
	else if(backupschedule==2)
	{
		//仅备份一次
		postdata+='&starttime='+$("#stime1").val();
	}
	else if(backupschedule==3)
	{
		//每天
		postdata+='&starttime='+$("#stime2").val();
		postdata+='&timeinterval='+$("#timeinterval").val();
	}
	else if(backupschedule==4)
	{
		//每周
		postdata+='&starttime='+$("#stime3").val();
		postdata+="&"+$("input[name='perweek']").serialize();
	}
	else if(backupschedule==5)
	{
		//每月
		postdata+='&starttime='+$("#stime4").val();
		for(var i=0;i<$("div.myoneday.myonedayselected").length;i++)
		{
			var monthly = $("div.myoneday.myonedayselected")[i].innerHTML;
			postdata+='&monthly='+monthly;
		}
	}

	// 备份重试
	var enable_backup_retry = $('#enable-backup-retry').is(':checked'),
		backup_retry_count = enable_backup_retry ? $('#retry-counts').val() : 5,
		backup_retry_interval = enable_backup_retry ? $('#retry-interval').val() : 10;
	postdata+='&backup_retry='+$.toJSON({enable:enable_backup_retry, count:backup_retry_count, interval:backup_retry_interval});
	postdata+='&retentionperiod='+get_data_keeps_deadline();
	postdata+='&retentionperiod_unit='+get_data_keeps_deadline_unit();
	postdata+='&cleandata='+$("#cleandata").val();
	postdata+="&backupmode="+backupmode;
	postdata+="&thread_count="+set_get_thread_count();
	postdata+="&BackupIOPercentage="+$('#BackupIOPercentage').val();
	{% if backupobj %}
	var backupobj = $("input[name='backupobj']:checked").val();
	//postdata+="&backupobj="+backupobj;
	if (backupobj=='2')
	{
		//卷备份
		postdata+='&volexclude='+getAciTreeBoxChecked('Servers_Tree2',false);
	}
	{% endif %}

	var usemaxbandwidth = "0";
	if( $('#usemaxbandwidth').prop("checked") )
	{
		usemaxbandwidth = "1";
	}
	postdata+='&usemaxbandwidth='+usemaxbandwidth;
	postdata+='&maxbandwidth='+$("#maxbandwidth").val();
	postdata+='&keepingpoint='+$("#keepingpoint").val();

	var vmware_tranport_modes = $("input[name='vmware_tranport_modes']:checked").val();
	postdata+='&vmware_tranport_modes='+vmware_tranport_modes;
	var vmware_quiesce = $('#vmware_quiesce').prop("checked") ? '1' : '0';
	postdata+='&vmware_quiesce='+vmware_quiesce;

    // 排除卷、磁盘情况：{'disk': [guid|lable, guid|lable], 'vol': [guid|lable, guid|lable]};
    var excludeDetails = {'disk': [], 'vol': []};
	var nodesUnChecked = getUnCheckedBox('disk-vol-tree');
	$.each(nodesUnChecked, function (index, elem) {
		if(elem.level == 0){
			excludeDetails['disk'].push(elem.id + '|' + elem.lable);
		}
		if(elem.level == 1){
			excludeDetails['vol'].push(elem.id + '|' + elem.lable);
		}
	});
	$.unique(excludeDetails['vol']);

    postdata+='&excludeDetails=' + $.toJSON(excludeDetails);

    // 是否去重系统文件夹
	postdata+='&SystemFolderDup=' + ($("#dup-sys-folder").prop("checked") ? 1 : 0);

	// "每天" 修改为 "按间隔时间"
	postdata+='&intervalUnit=' + $('#interval-unit :selected').val();

	// 其他的参数: 脚本信息
	postdata+=other_params;

	if( tasktype == 'backup' )
	{
		if($('#taskid').val())
		{
			postdata+='&taskid='+$('#taskid').val();
			openConfirmDialog({
				title:'确认信息',
				html:'你确定要更改此项计划吗?',
				onBeforeOK:function(){
					postdata+="&a=createbackup&immediately=0";
					myAjaxPost('../backup_handle/',postdata,CreateBackupCallback,0);
					$(this).dialog('close');
				}
			});
			return;
		}

		$("#finishFrom").attr('title','完成').dialog({
			autoOpen: true,
			height: 200,
			width: 400,
			modal: true,
			buttons: {
				'完成设置': function(){
					postdata+="&a=createbackup&immediately=0";
					myAjaxPost('../backup_handle/',postdata,CreateBackupCallback,0);
					$(this).dialog('close');
				},
				'立即备份': function(){
					postdata+="&a=createbackup&immediately=1";
					myAjaxPost('../backup_handle/',postdata,CreateBackupCallback,1);
					$(this).dialog('close');
				}
			},
			close: function(){
				$("#newbackupFormDiv").dialog();
				$("#newbackupFormDiv").dialog('destroy');
			}
		});
	}
	else // 将创建, 更改策略
	{
		if($('#taskid').val())
		{
			postdata+='&id='+$('#taskid').val();
			openConfirmDialog({
				title:'确认信息',
				html:'你确定要更改此项策略吗?',
				onBeforeOK:function(){
					postdata+="&a=editpolicy";
					myAjaxGet('../backup_handle/',postdata,CreateBackupCallback,0);
					$(this).dialog('close');
				}
			});
			return;
		}

		postdata+="&a=createpolicy";
		myAjaxGet('../backup_handle/',postdata,CreateBackupCallback,0);
	}
}

function setPreControlStatus(selindex)
{
	if(selindex==1)
	{
		$(".prev").hide();
	}
	if( selindex==2 && $('#uitasktype').val() == 'uitaskpolicy' )
	{
		$(".prev").hide();
	}
	$('#next').attr("value","下一步»");
}

function setControlStatus(selindex)
{
	if(selindex == 0 )
	{
	}
	else if(selindex==2)
	{
		var backupschedule=$("input[name='bakschedule']:checked").val();
		if(backupschedule==1)
		{
			$('#cdpperiod').attr("disabled",false);
			$("input[name='cdptype']").attr("disabled",false);
		}
		else
		{
			$('#cdpperiod').attr("disabled",true);
			$("input[name='cdptype']").attr("disabled",true);
		}
		var hostIdent = getaciTreeChecked('Servers_Tree1');
		// 新建策略时
		if(hostIdent == ''){
			$('label[for=advanced2-manage]').hide();
		}
		// 创建、更改计划时
		else {
			$('label[for=advanced2-manage]').show();
			// 1.创建计划时，获取最新磁盘，卷的信息
			// 2.更改计划时，获取最新磁盘，卷的信息(且剔除了已经排除的磁盘、卷)
			// planId: 'id'、undefined	更改计划对话框，打开期间--'id'；关闭--undefined
			var planId = window.IdOfChangePlan;
			var url = '../backup_handle/?a=diskvolinfo&ident={0}&planId={1}'.replace('{0}', hostIdent).replace('{1}', planId);
			refreshDiskAciTree('disk-vol-tree', url);
			$('#waiting-tree-data').html('请稍候，读取数据中...');
		}
	}
	else if(selindex==4) // 确认参数界面
	{
		$('#next').val('完成');
		var taskname=$('#taskname').val();
		var storagedevice= $("#storagedevice").find("option:selected").text();
		var backuptype=1;
		$('#confirm_taskname').html(taskname);
		$('#confirm_storagedevice').html(storagedevice);
		$('#confirm_backuptype').html('整机备份');

		var servername=getaciTreeNameChecked('Servers_Tree1');
		$('#confirm_src').html(servername);
		var schedule="";
		var backupschedule=$("input[name='bakschedule']:checked").val();
		$('#confirm_cdpperiod').html('-');
		$('#confirm_cdptype').html('-');
		if(backupschedule==1)
		{
			schedule='连续数据保护（CDP），开始时间：' + $("#stime0").val();
			var cdpperiod= $("#cdpperiod").find("option:selected").text();
			$('#confirm_cdpperiod').html(cdpperiod);
			var tmp=$("input[name='cdptype']:checked").val();
			if(tmp==0)
			{
				cdptype='数据优先';
			}
			else if(tmp==1)
			{
				cdptype='业务优先';
			}
			$('#confirm_cdptype').html(cdptype);
		}
		else if(backupschedule==2)
		{
			schedule='仅备份一次，开始时间：'+$("#stime1").val();
		}
		else if(backupschedule==3)
		{
			schedule='按间隔时间，开始时间：'+$("#stime2").val()+'，每'+$("#timeinterval").val()+'{0}开始执行';
			schedule = schedule.replace('{0}', $('#interval-unit :selected').text());
		}
		else if(backupschedule==4)
		{
			var tmp=new Array();
			for(var i=0;i<$("input[name='perweek']:checked").length;i++)
			{
				var n=parseInt($("input[name='perweek']:checked")[i].value);
				switch(n)
				{
					case 1:
						tmp.push("星期一");
						break;
					case 2:
						tmp.push("星期二");
						break;
					case 3:
						tmp.push("星期三");
						break;
					case 4:
						tmp.push("星期四");
						break;
					case 5:
						tmp.push("星期五");
						break;
					case 6:
						tmp.push("星期六");
						break;
					case 7:
						tmp.push("星期日");
						break;
				}
			}
			schedule='每周，开始时间：'+$("#stime3").val()+'，每周'+tmp.join('、')+"备份";
		}
		else if(backupschedule==5)
		{
			var tmp=new Array();
			for(var i=0;i<$("div.myoneday.myonedayselected").length;i++)
			{
				tmp.push($("div.myoneday.myonedayselected")[i].innerHTML);
			}
			schedule='每月，开始时间：'+$("#stime4").val()+'，每月'+tmp.join(',')+"日备份";
		}
		$('#confirm_schedule').html(schedule);
		if(get_data_keeps_deadline_unit() === 'day'){
			var retentionperiod=$("#retentionperiod").val() + '天';
		}
		else {
			var retentionperiod=$("#retentionperiod").val() + '个月';
		}

		$('#confirm_retentionperiod').html(retentionperiod);
		var cleandata= $("#cleandata").val() + 'GB';
		$('#confirm_cleandata').html(cleandata);
		var maxbandwidth= $("#maxbandwidth").val();
		$('#confirm_maxbandwidth').html('无限制');
		var usemaxbandwidth = 1;
		if( $('#usemaxbandwidth').prop("checked") )
		{
			usemaxbandwidth = 0;
			$('#confirm_maxbandwidth').html(maxbandwidth+"Mbit/s");
		}

		var vmware_tranport_modes = $("input[name='vmware_tranport_modes']:checked").val();
		switch(vmware_tranport_modes)
		{
			case '1':
				$('#confirm_vmware_tranport_modes').html('自动');
				break;
			case '2':
				$('#confirm_vmware_tranport_modes').html('SAN');
				break;
			case '3':
				$('#confirm_vmware_tranport_modes').html('HotAdd');
				break;
			case '4':
				$('#confirm_vmware_tranport_modes').html('NBD');
				break;
		}

		var vmware_quiesce = $('#vmware_quiesce').prop("checked") ? '1' : '0';
		if(vmware_quiesce=='1')
		{
			$('#confirm_vmware_quiesce').html('是');
		}
		else
		{
			$('#confirm_vmware_quiesce').html('否');
		}

		$('#confirm_encipher').html($('#isencipher').val());
		var backupmode = $('#full-bak-everytime').prop("checked") ? '1' : '2';
		switch(backupmode)
		{
			case '1':
				$('#confirm_backupmode').html('每次都完整备份');
				break;
			case '2':
				$('#confirm_backupmode').html('仅第一次进行完整备份，以后增量备份');
				break;
		}
		var keepingpoint=parseInt($('#keepingpoint').val());
		$('#confirm_keepPointNum').text(keepingpoint + '个');
		$('#confirm_dupFolder').text($("#dup-sys-folder").prop("checked")?'是':'否');

		// 备份重试
		var enable_backup_retry = $('#enable-backup-retry').is(':checked'),
			backup_retry_count = $('#retry-counts').val(),
			backup_retry_interval = $('#retry-interval').val();
		if (enable_backup_retry){
			$('#confirm_backup_retry').text('启用；重试间隔：'+ backup_retry_interval +'分钟；重试次数：'+ backup_retry_count + '次');
		}else{
			$('#confirm_backup_retry').text('禁用');
		}
		if(!is_norm_plan()){
			$('#confirm_backup_retry').text('-');
		}

		// 线程信息
		$('#confirm_thread_count').text(set_get_thread_count());

		// 排除磁盘、卷信息确认
		var nodesUnChecked = getUnCheckedBox('disk-vol-tree');
		var excludeDisks = [];
		var excludeVols = [];
		$.each(nodesUnChecked, function (index, elem) {
			if(elem.level == 0){
				excludeDisks.push(elem.lable);
			}
			if(elem.level == 1){
				excludeVols.push(elem.lable);
			}
		});
		$.unique(excludeVols);
		excludeDisks = excludeDisks.length > 0 ? excludeDisks.join('<br>') : '-';
		excludeVols = excludeVols.length > 0 ? excludeVols.join('<br>') : '-';

		$('#confirm_excludeDisks').html(excludeDisks);
		$('#confirm_excludeVols').html(excludeVols);
	}
}

$('#prev').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');
	var id = getaciTreeChecked('Servers_Tree1');
	var agent_type = GetAciTreeValueRadio('Servers_Tree1',id,'type');
	if(selindex==2)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 备份信息</div>');
	}
	else if(selindex==1)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 备份源</div>');
	}
	else if(selindex==3)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 备份计划</div>');
	}
	else if(selindex==4)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 其他设置</div>');
		var backuppolicy = $('#backuppolicy').val();
		if( backuppolicy != -1 )
		{
			selindex = 2;
		}
	}
	else if(selindex==5){
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 执行脚本</div>');
	}

	if(selindex ==0)
	{
	}
	if( selindex-1 ==2 && $('#uitasktype').val() == 'uitaskpolicy' )
	{
		$(".prev").hide();
	}

	if(agent_type==2 && selindex==5)
	{
		//免代理，且使用策略
		var backuppolicy = $('#backuppolicy').val();
		if( backuppolicy != -1 )
		{
			selindex=2;
		}
		else
		{
			selindex=4;
		}
	}
	$( "#tabs" ).tabs( "option", "active", selindex-1 );
	setPreControlStatus(selindex);

	if(selindex==5 && $('#uitasktype').val() == 'uitaskpolicy'){
		$(".prev").trigger('click');
	}

});
function Gebackuppolicy(retjson)
{
	if(retjson.r!=0)
	{
		openErrorDialog({title:'错误',html:retjson.e});
		return;
	}
	var id = getaciTreeChecked('Servers_Tree1');
	var agent_type = GetAciTreeValueRadio('Servers_Tree1',id,'type');
	$("#backuppolicy").empty();
	$("#backuppolicy").append("<option value='-1' selected='selected' >--请选择策略--</option>");
	$.each(retjson.list, function(i, item){
		if(agent_type == 2)
		{
			if(item.cycletype==1)
			{
				//免代理不支持CDP类型的策略
				return false;
			}
		}
		$("#backuppolicy").append("<option value='"+item.id+"'>"+item.name+"</option>");
	});
}

function confirm_dialog()
{
	$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 信息确认</div>');
    	var shell_infos = get_shell_infos_from_ui($('#tabs-shell'));
    	if(is_enable_shell($('#tabs-shell'))){
            confirm_shell.exe_name = shell_infos.exe_name;
            confirm_shell.params = shell_infos.params;
            confirm_shell.work_path = shell_infos.work_path;
            confirm_shell.unzip_path = shell_infos.unzip_path;
            confirm_shell.zip_file = (shell_infos.zip_file === '')?('-'):(shell_infos.zip_file);
            confirm_shell.ignore_shell_error = (shell_infos.ignore_shell_error)?('是'):('否')
        }
        else {
            confirm_shell.exe_name = '-';
            confirm_shell.params = '-';
            confirm_shell.work_path = '-';
            confirm_shell.unzip_path = '-';
            confirm_shell.zip_file = '-';
            confirm_shell.ignore_shell_error = '-';
        }
}

function my_next(selindex) {
    $("#tabs").tabs("option", "active", selindex + 1);
    setControlStatus(selindex);
    if (selindex == 0) {
        $(".prev").show();
    }

    if (selindex == 2 && $('#uitasktype').val() == 'uitaskpolicy') {
        $(".prev").show();
    }

    if (selindex == 3 && $('#uitasktype').val() == 'uitaskpolicy') {
        $('.next').trigger('click');
    }
}

$('#next').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');
	var id = getaciTreeChecked('Servers_Tree1');
	var agent_type = GetAciTreeValueRadio('Servers_Tree1',id,'type');
	if(agent_type == 2 || $('#uitasktype').val() == 'uitaskpolicy')
	{
		//免代理或策略
		$('#shell_info_tr_id').hide();
	}
	else
	{
		$('#shell_info_tr_id').show();
	}
	if(selindex==0)
	{
		myAjaxGet('../backup_handle/','a=getstoragedevice',Getstoragedevice);
		myAjaxGet('../backup_handle/','a=getpolicy',Gebackuppolicy);
		var hostName = getaciTreeNameChecked('Servers_Tree1');
		var planName = '备份' + hostName + CurentTime();
		if(!isInEdit()){
			$('#taskname').val(planName);
		}
		if(agent_type == 2)
		{
			//免代理
			$("input[name=bakschedule][value=1]").prop("disabled",true);
			$('#bakschedule1').hide();
			$('.vmware_div').show();
			$('#security-manage').hide();
			$('#security-manage-lab').text('▶');
			var backupschedule=$("input[name='bakschedule']:checked").val();
			if(backupschedule==1)
			{
				$("input[name=bakschedule][value=2]").prop("checked",true);
			}
		}
		else
		{
			$('.vmware_div').hide();
			$('#security-manage').show();
			$('#security-manage-lab').text('▼');
		}
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 备份信息</div>');
	}
	else if(selindex==1)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 备份计划</div>');
		$('#stime0').val() || $('#stime0').val(curTime);
		$('#stime1').val() || $('#stime1').val(curTime);
		$('#stime2').val() || $('#stime2').val(curTime);
		$('#stime3').val() || $('#stime3').val(curTime);
		$('#stime4').val() || $('#stime4').val(curTime);
		var backuppolicy = $('#backuppolicy').val();
		if( backuppolicy != -1 )
		{
			if(agent_type == 2)
			{
				//免代理
				$( "#tabs" ).tabs( "option", "active", 5 );
				confirm_dialog();
				setControlStatus(4);
			}
			else
			{
				$( "#tabs" ).tabs( "option", "active", 4 );
				setControlStatus(3);
			}
			
			return;
		}

	}
	else if(selindex==2)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 其他设置</div>');
		if(is_norm_plan()){
			retry_item_enable();
		}
		else {
			retry_item_disable();
		}

		if(!isInEdit()){
			if($('#uitasktype').val() != 'uitaskpolicy'){
				set_data_keeps_deadline('1');
				set_data_keeps_deadline_unit('month');
				set_get_thread_count('4');
			}
		}
        set_retentionperiod_msg(false);
	}
	else if(selindex==3)
	{
		$('#navigation').html('<div class="font_navigation">备份 > 新建备份计划 > 执行脚本</div>');
	}
	else if(selindex==4)
	{
		confirm_dialog();
	}

	if(selindex==5)
	{
		if(is_enable_shell_and_new_zip($('#tabs-shell'))){			// 启用脚本功能, 当前上传了文件
			uplodePreBackupShell($('#preBackupShell'), '../hotbackup_handle/?a=upload_script', uplodeCallbk);
		}
		else if(is_enable_shell_and_not_zip($('#tabs-shell'))){		// 启用脚本功能, 当前没有上传文件, 针对上一次上传了的情况
			uplodeCallbk(true, 'use_last_zip')
		}
		else {
			OnFinish('');											// 未启用脚本功能
		}
	}
	else
	{
		if(CheckData(selindex))
		{
			if(selindex==3 && agent_type == 2)
			{
				//免代理
				selindex = 4;
			}

			//
			if(selindex==3 && agent_type != 2)
			{
				var maxbandwidth=$('#maxbandwidth').val(),
					BackupIOPercentage=$('#BackupIOPercentage').val(),
					warning_str='';
				var msg = '<p style="text-indent: 2em">当前配置<span style="color: red">[{warning_str}]</span>在备份时资源占用可能过大，' +
					'可能会引起备份源主机上业务系统出现响应缓慢或超时等问题，请确认是否有这样的风险，没有风险请输入<span style="color: red">OK</span>并点击确认按钮。</p>' +
					'<input type="text" id="confirm_input" style="width: 100%" onblur="removespace(this)">'+
                    '<p style="color: grey">提示：备份过程中也可以调整资源占用。</p>'
				if (maxbandwidth > 300){
					warning_str += '(占用源主机的网络带宽超过300Mbit/s)';
				}
				if (maxbandwidth == -1){
					warning_str += ' (占用源主机的网络带宽为 不限制)';
				}
				if (BackupIOPercentage > 30){
					warning_str += ' (占用源主机的存储性能的百分比超过30%)';
				}
				if (warning_str){
					openConfirmDialog({
						html:msg.replace('{warning_str}', warning_str),
						width:580,
						height:'auto',
						onBeforeOK:function () {
							if ($('#confirm_input').val().toLowerCase() == 'ok'){
								my_next(selindex);
								$(this).dialog('close');
							}else {
								return;
							}
						}
					})
					return;
				}
			}
            my_next(selindex);
        }
	}
});


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
}


$("input[name='bakschedule']").click(function() {
	if(this.value==1)
	{
		$('#bakschedule1').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule4').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==2)
	{
		$('#bakschedule1').slideUp(600);
		$('#bakschedule2').slideDown(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule4').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==3)
	{
		$('#bakschedule1').slideUp(600);
		$('#bakschedule3').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule4').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==4)
	{
		$('#bakschedule1').slideUp(600);
		$('#bakschedule4').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule5').slideUp(600);
	}
	else if(this.value==5)
	{
		$('#bakschedule1').slideUp(600);
		$('#bakschedule5').slideDown(600);
		$('#bakschedule2').slideUp(600);
		$('#bakschedule3').slideUp(600);
		$('#bakschedule4').slideUp(600);
	}

});

$('#RefreshSrcServer').button().click(function(){

	var param = '';
	if ($('input[name=noplan]').is(':checked'))
	{
		param += '&noplan=noplan';
		
	}
	if ($('input[name=groupview]').is(':checked'))
	{
		param += '&group=group';
	}

	RefreshAciTree("Servers_Tree1",'../backup_handle/?a=getlist'+param+'&id=');
});

{% if backupobj %}

function RefreshSrcServer2()
{
	var serverid = getaciTreeChecked('Servers_Tree1');
	RefreshAciTree("Servers_Tree2",'../backup_handle/?a=getVollist&serverid='+serverid+'&id=');
}

$('#RefreshSrcServer2').button().click(function(){
	RefreshSrcServer2();
});

{% endif %}


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
	if(eventName === 'opened') {
		var hostName = $.cookie('default_host_name_when_create_plan');
		if (hostName){
			CheckAciTreeRadioByLabel('Servers_Tree1', hostName);
			$.cookie('default_host_name_when_create_plan', null, {path: '/'});
		}
	}
	if(eventName!='selected')
	{
		return;
	}
	var id=api.getId(item);
	if(id!=undefined && id.substring(0,3)!='ui_' && id.substring(0,6)!="group_")
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

function setmaxbandwidthstatus()
{
	if($('#usemaxbandwidth').prop('checked'))
	{
		$('#maxbandwidth').prop('disabled',false);
	}
	else
	{
		$('#maxbandwidth').prop('disabled',true);
	}
}

$('#usemaxbandwidth').click(function(){
	setmaxbandwidthstatus();
});

// 后台获取策略数据，回调方法
function GetpolicydetailCallback(jsonstr)
{
	if(jsonstr.r!=0)
	{
		openErrorDialog({title:'错误',html:jsonstr.e});
		return;
	}
	switch(jsonstr.cycletype)
	{
		case 2:
			$($("input[name=bakschedule]")[1]).click();
			$("#stime1").val(jsonstr.starttime);
			break;
		case 3:
			$($("input[name=bakschedule]")[2]).click();
			$("#stime2").val(jsonstr.starttime);
			$("#timeinterval").val(jsonstr.timeinterval);
			$('#interval-unit').val(jsonstr.unit);
			break;
		case 4:
			$($("input[name=bakschedule]")[3]).click();
			$("#stime3").val(jsonstr.starttime);
			var pes=jsonstr.period.split(',');
			for(var i=0;i<pes.length;i++)
			{
				$($("input[name=perweek]")[pes[i]-1]).prop("checked",true);
			}
			break;
		case 5:
			$($("input[name=bakschedule]")[4]).click();
			$("#stime4").val(jsonstr.starttime);
			var pes=jsonstr.monthly.split(',');
			for(var i=0;i<pes.length;i++)
			{
				for(var j=0;j<$("div.myoneday").length;j++)
				{
					var monthly = $("div.myoneday")[j].innerHTML;
					if(monthly==pes[i])
					{
						$($("div.myoneday")[j]).addClass("myonedayselected");
						break;
					}
				}
			}
			break;
		default:
			$($("input[name=bakschedule]")[0]).click();
			if(jsonstr.starttime){
				$("#stime0").val(jsonstr.starttime);
			}
			else {
				$("#stime0").val(curTime);
			}
	}

	if(jsonstr.retentionperiod_unit === 'day'){
        set_data_keeps_deadline_unit('day');
        set_data_keeps_deadline(jsonstr.retentionperiod);
    }
    else if (jsonstr.retentionperiod_unit === 'month'){
        set_data_keeps_deadline_unit('month');
        set_data_keeps_deadline(jsonstr.retentionperiod / 30);
    }
    else {
        set_data_keeps_deadline_unit('month');
        set_data_keeps_deadline(jsonstr.retentionperiod / 30);
    }

    set_get_thread_count(jsonstr.thread_count);

	$("#cleandata").val(jsonstr.cleandata);
	if(jsonstr.cycletype == 1){
		$("#cdpperiod").val(jsonstr.cdpperiod);
	}
	$("#maxbandwidth").val(jsonstr.maxbandwidth);
	if( jsonstr.usemaxbandwidth == 0  )
	{
		$("#maxbandwidth").prop("disabled",true);
		$("#usemaxbandwidth").prop("checked",false);
	}
	else
	{
		$("#maxbandwidth").prop("disabled",false);
		$("#usemaxbandwidth").prop("checked",true);
	}
	$('#keepingpoint').val(jsonstr.keepingpoint);
	if(jsonstr.cdptype==0)
	{
		$("input[name=cdptype][value=0]").click();
	}
	else
	{
		$("input[name=cdptype][value=1]").click();
	}
	if(jsonstr.isencipher==1)
	{
		$('#isencipher').val("加密");
	}
	else
	{
		$('#isencipher').val("不加密");
	}

	if(jsonstr.backupmode==1)
	{
		$("input[name='backupmode'][value=1]").prop("checked",true);
	}
	else
	{
		$("input[name='backupmode'][value=2]").prop("checked",true);
	}

	$('#dup-sys-folder').prop('checked', jsonstr.removeDup);
}

// 新建计划，选择备份策略时
$('#backuppolicy').change(function(){
	var id=$(this).children('option:selected').val();
	if( id == -1 )
	{
		return;
	}
	var params="a=getpolicydetail&id="+id;
	myAjaxGet('../backup_handle/',params,GetpolicydetailCallback);
});


$(function()
{
	$('#bakschedule2').hide();
	$('#bakschedule3').hide();
	$('#bakschedule4').hide();
	$('#bakschedule5').hide();
	for(var i=1;i<=31;i++)
	{
		$('#dayselect').append('<div class="myoneday">'+i+'</div>');
	}

	$("div.myoneday").click(function(){
		if($(this).hasClass("myonedayselected"))
		{
			$(this).removeClass("myonedayselected");
		}
		else
		{
			$(this).addClass("myonedayselected");
		}
	});
});


String.prototype.endWith=function(endStr){
  var d=this.length-endStr.length;
  return (d>=0&&this.lastIndexOf(endStr)==d);
};

function checkParent(api, child_item) {
	var parent = api.parent(child_item);
	if(!api.isChecked(parent)){
		api.check(parent);
	}
}

// 遍历AciTree，将所有符合child_id的父，选中
function checkParentById(tree_id, child_id) {
	var api = $("#" + tree_id).aciTree('api');
	var childs = api.children(null,true,true);
	$.each(childs, function (index, item) {
        var childNode = $(item);
		if(child_id == api.getId(childNode)){
			checkParent(api, childNode);
		}
    });
}

var myFunc = function(event, api, item, eventName, options) {
	// 数据读取完成
	if(eventName == 'init'){
		$('#waiting-tree-data').html('');
		var childs = api.children(null,true,true);
		if(api.enabled(childs).length == 0){
			$('#waiting-tree-data').html('<span style="color: #ff0000">获取磁盘信息失败，不能设置备份区域</span>');
		}
	}

	// 选择、取消子元素: 处理跨盘卷的情况
	if((eventName == 'checked' || eventName == 'unchecked') && api.level(item) == 1){
		var checked_id = getAciTreeBoxChecked('disk-vol-tree', true);
		var unchecked_id = getAciTreeBoxChecked('disk-vol-tree', false);
		checked_id = (checked_id == '') ? [] : checked_id.split(',');
		unchecked_id = (unchecked_id == '') ? [] : unchecked_id.split(',');
		var intersection = checked_id.filter(function (cked_id) {
			return $.inArray(cked_id, unchecked_id) > -1;
		});
		intersection = $.unique(intersection);
		if(eventName == 'checked' && intersection.length > 0){
			$('#disk-vol-tree').off('acitree');
			$.each(intersection, function (index, id) {
				CheckAciTreeBox('disk-vol-tree', id, true);
				checkParentById('disk-vol-tree', id);
			});
			$('#disk-vol-tree').on('acitree', myFunc);
		}
		if(eventName == 'unchecked' && intersection.length > 0){
			$('#disk-vol-tree').off('acitree');
			$.each(intersection, function (index, id) {
				CheckAciTreeBox('disk-vol-tree', id, false);
			});
			$('#disk-vol-tree').on('acitree', myFunc);
		}
	}
	// 选择子元素：父必选择
	if(eventName == 'checked' && api.level(item) == 1){
		checkParent(api, item);
	}
	// 取消父元素：有孩子、父为Boot，则禁止取消父
	if(eventName == 'unchecked' && api.level(item) == 0){
		var isBoot = String(api.getLabel(item)).endWith('(启动盘)');
		$.each(api.children(item), function (index, elem) {
			var child = $(elem);
			if(api.isChecked(child) || isBoot){
				api.check(item);
				return false;
			}
		});
	}
};

$('#disk-vol-tree').on('acitree', myFunc);


function initDiskAciTree(treeid,url)
{
	return $('#'+treeid).aciTree({
			autoInit: true,
			ajax: {
				url: url
			},
			checkbox: true,			// 初始：复选框、可选的
			selectable: true,
            checkboxChain: false	// 父子独立
		}).aciTree('api');
}

function refreshDiskAciTree(treeid,url)
{
	$('#'+treeid).aciTree().aciTree('api').destroy({
		success: function() {
			initDiskAciTree(treeid,url);
		}
	});
}

function isInEdit() {
	return window.IdOfChangePlan!=undefined;
}

var confirm_shell = new Vue({
	el: '#confirm_shell',
	data: {
		exe_name : '',
		params: '',
		work_path: '',
		unzip_path: '',
		zip_file: '',
		ignore_shell_error: ''
	}
});

function uplodeCallbk(status, err_o_path) {
	if(status){
		var shell_infos = get_shell_infos_from_ui($('#tabs-shell'));
		shell_infos.zip_tmp_path = err_o_path;
		var other_params = '&shell_infos=' + $.toJSON(shell_infos);
		OnFinish(other_params);
	}
	else {
		openErrorDialog({title:'错误', html:err_o_path});
	}
}

function is_norm_plan() {
	var checked_radio = $('input[type=radio][name=bakschedule]:checked');
	return checked_radio.val() !== '1';
}

function retry_item_enable() {
	$('label[for=advanced3-manage]').show();
	$('#the-br-for-retry').show();
	$('#advanced3-manage').hide();
}

function retry_item_disable() {
	$('label[for=advanced3-manage]').hide();
	$('#the-br-for-retry').hide();
	$('#advanced3-manage').hide();
}

function get_data_keeps_deadline() {
    return $('#retentionperiod').val();
}

function set_data_keeps_deadline(val) {
    $('#retentionperiod').val(val);
}

function get_data_keeps_deadline_unit() {
    return $('#retentionperiod-unit').val();
}

function set_data_keeps_deadline_unit(unit) {
    $('#retentionperiod-unit').val(unit);
}

$('#retentionperiod-unit').on('change', function () {
    set_retentionperiod_msg(true);
});

function set_retentionperiod_msg(auto) {
    if($('#retentionperiod-unit').val() === 'day'){
    	if(parseInt($("#tabs-3 .radio:checked").val()) == 1){
    		$('#retentionperiod-msg').text('5-300天');
    		$('#retentionperiod').attr({'min':5,'max':300});
        	if(auto){set_data_keeps_deadline('5');}
		}else{
    		$('#retentionperiod-msg').text('3-360天');
    		$('#retentionperiod').attr({'min':3,'max':360});
        	if(auto){set_data_keeps_deadline('3');}
		}
    }
    else {
        $('#retentionperiod-msg').text('1-240月');
        $('#retentionperiod').attr({'min':1,'max':240});
        if(auto){set_data_keeps_deadline('1');}
    }
}

function set_get_thread_count(cnt) {
	if(cnt){
		$('#thread-count').val(cnt);
	}
	else {
		return $('#thread-count').val();
	}
}

$('#noplan').click(function(){
	if(isInEdit())
	{
		return;
	}
	var noplan = $(this).is(':checked');
	var param = '';
	if(noplan)
	{
		$.cookie('noplan', 'true', {path: '/'});
		param += '&noplan=noplan';
	}
	else
	{
		$.cookie('noplan', null, {path: '/'});
	}

	if ($('input[name=groupview]').is(':checked'))
	{
		param += '&group=group';
	}

	RefreshAciTree("Servers_Tree1",'../backup_handle/?a=getlist'+param+'&id=');
});

$('#groupview').click(function(){
	if(isInEdit())
	{
		return;
	}
	var groupview = $(this).is(':checked');
	var param = '';
	if(groupview)
	{
		$.cookie('groupview', 'true', {path: '/'});
		param += '&group=group';
	}
	else
	{
		$.cookie('groupview', null, {path: '/'});
	}

	if ($('input[name=noplan]').is(':checked'))
	{
		param += '&noplan=noplan';
	}

	RefreshAciTree("Servers_Tree1",'../backup_handle/?a=getlist'+param+'&id=');
});