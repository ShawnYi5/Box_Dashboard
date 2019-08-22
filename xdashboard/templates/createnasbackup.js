function CreateBackupCallback(jsonstr,immediately)
{
	var curPage = '{{ page }}';
	if(jsonstr.r!=0)
	{
		try
		{
			var new_url = '../backupmgr_handle/?a=list&backup_source_type=4';
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
		var new_url = '../backupmgr_handle/?a=list&backup_source_type=4';
		$('#list').setGridParam({url:new_url});
		$('#list').trigger("reloadGrid",[{page:1}]);
	}
	catch (e)
	{
	}

	if(curPage == 'createbackup'){
		var html='新建备份计划成功，您可在<a href="../mgrbackup" style="color:blue;">备份计划管理</a>中查看已创建的计划任务。';
		if(immediately==1)
		{
			var html='新建备份计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况，或在<a href="../mgrnasbackup" style="color:blue;">NAS备份计划管理</a>中管理已创建的备份计划。';
		}
		openSuccessDialog({title:'完成',html:html});
	}
	else if(curPage == 'mgrbackup'){
		if(immediately==1)
		{
			var html='新建备份计划成功，您可在<a href="../home" style="color:blue;">系统状态</a>中查看任务执行情况。';
			openSuccessDialog({title:'完成',html:html});
		}

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
		$('#enum_threads').val('2');
		$('#sync_threads').val('4');
		$('#cores').val('2');
		$('#memory_mbytes').val('512');
		$('#net_limit').val('-1');
		$('#enum_level').val('4');
		$('#sync_queue_maxsize').val('256');
	}
	$('#stime0').val() || $('#stime0').val(curTime);
	$('#stime1').val() || $('#stime1').val(curTime);
	$('#stime2').val() || $('#stime2').val(curTime);
	$('#stime3').val() || $('#stime3').val(curTime);
	$('#stime4').val() || $('#stime4').val(curTime);
	$("div.myoneday").removeClass("myonedayselected");
	$(".prev").hide();
	$('#next').attr("value","下一步»");
	$($("input[name=bakschedule]")[1]).click();
	$("input[name=perweek]").prop("checked",false);
	$('#bakschedule2').hide();
	$('#bakschedule3').hide();
	$('#bakschedule4').hide();
	$('#bakschedule5').hide();
	$('#usemaxbandwidth').prop('checked',true);
	$('#maxbandwidth').val(300);

	$('#enable-backup-retry').prop('checked',true);
	$('#retry-counts').val(5);
	$('#retry-interval').val(10);

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
		var nas_username = $('#nas_username').val();
		var nas_password = $('#nas_password').val();
		var nas_password2 = $('#nas_password2').val();
		var nas_path = $('#nas_path').val();
		var nas_protocol = $("input[name='nas_protocol']:checked").val().toUpperCase();
		if(nas_protocol=='CIFS')
		{
			if(nas_username=='')
			{
				openErrorDialog({title:'错误',html:'请输入用户名。'});
				return false;
			}
			if(nas_password=='')
			{
				openErrorDialog({title:'错误',html:'请输入密码。'});
				return false;
			}
			if(nas_password!=nas_password2)
			{
				openErrorDialog({title:'错误',html:'两次密码输入不一致。'});
				return false;
			}
			
			if(!isCIFSpath(nas_path))
			{
				openErrorDialog({title:'错误',html:'请输入正确的NAS路径，例如：\\\\\\192.168.0.1\\folder'});
				return false;
			}
		}
		else
		{
			if(!isNFSpath(nas_path))
			{
				openErrorDialog({title:'错误',html:'请输入正确的NAS路径，例如：192.168.0.1:/folder'});
				return false;
			}
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
        $('#advanced2-manage').hide();
        $('#advanced4-manage').hide();
        $('#advanced1-manage_tt1').hide();
        $('#advanced2-lab').text('▶');
        $('#advanced4-lab').text('▶');

		$('#nas-base-manage').hide();
        $('#nas-adv-manage').hide();
        $('#nas-base-lab').text('▶');
        $('#nas-adv-lab').text('▶');
        $('#nas_max_space_val_tip').html($('#nas_path').val());

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

		var nas_space = $('#nas_max_space_val').val();
		if (!isNum(nas_space)||nas_space<1){
			openErrorDialog({title:'错误',html:'**无效的“共享文件夹存储容量大小”**  \n输入无效，请重新输入'});
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
function OnFinish()
{
	var tasktype = 'backup';
	var postdata='taskname='+encodeURIComponent($("#taskname").val());
	var backuptype=4;//BACKUP_FILES NAS类型
	postdata+='&backuptype='+backuptype;
	postdata+='&storagedevice='+$("#storagedevice").val();
	var backupschedule=$("input[name='bakschedule']:checked").val();
	postdata+='&bakschedule='+backupschedule;
	var backupmode = $('#full-bak-everytime').prop("checked") ? '1' : '2';
	if(backupschedule==2)
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

	var usemaxbandwidth = "0";
	if( $('#usemaxbandwidth').prop("checked") )
	{
		usemaxbandwidth = "1";
	}
	postdata+='&usemaxbandwidth='+usemaxbandwidth;
	postdata+='&maxbandwidth='+$("#maxbandwidth").val();
	postdata+='&keepingpoint='+$("#keepingpoint").val();

    // 是否去重系统文件夹
	postdata+='&SystemFolderDup=' + ($("#dup-sys-folder").prop("checked") ? 1 : 0);

	// "每天" 修改为 "按间隔时间"
	postdata+='&intervalUnit=' + $('#interval-unit :selected').val();

	postdata+='&nas_protocol='+$("input[name='nas_protocol']:checked").val().toUpperCase();
	postdata+='&nas_username='+$('#nas_username').val();
	postdata+='&nas_password='+$('#nas_password').val();
	postdata+='&nas_exclude_dir='+encodeURIComponent($('#nas_exclude_dir').val());
	postdata+='&nas_path='+encodeURIComponent($('#nas_path').val());

	var excludeDetails = {'disk': [], 'vol': []};
	postdata+='&excludeDetails=' + $.toJSON(excludeDetails);

	// nas相关参数
	var nas_params = $.param({
		enum_threads: $('#enum_threads').val(),
		sync_threads: $('#sync_threads').val(),
		cores: $('#cores').val(),
		memory_mbytes: $('#memory_mbytes').val(),
		net_limit: $('#net_limit').val(),
		enum_level: $('#enum_level').val(),
		sync_queue_maxsize: $('#sync_queue_maxsize').val(),
		nas_max_space_val: $('#nas_max_space_val').val(),
		nas_max_space_unit: $('#nas_max_space_unit').val()
	});
	postdata+='&'+nas_params;

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
}

function setPreControlStatus(selindex)
{
	if(selindex==1)
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
	}
	else if(selindex==3) // 确认参数界面
	{
		$('#next').val('完成');
		var taskname=$('#taskname').val();
		var storagedevice= $("#storagedevice").find("option:selected").text();
		$('#confirm_taskname').html(taskname);
		$('#confirm_storagedevice').html(storagedevice);
		$('#confirm_nas').html('协议：'+$("input[name='nas_protocol']:checked").val().toUpperCase()+'，NAS路径：'+$('#nas_path').val());
		var nas_exclude_dir = $('#nas_exclude_dir').val();
		if(nas_exclude_dir=='')
		{
			$('#confirm_nas_exclude_dir').html('-');
		}
		else
		{
			$('#confirm_nas_exclude_dir').html(nas_exclude_dir);
		}

		var schedule="";
		var backupschedule=$("input[name='bakschedule']:checked").val();
		$('#confirm_cdpperiod').html('-');
		$('#confirm_cdptype').html('-');
		if(backupschedule==2)
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

		// 线程信息
		$('#confirm_thread_count').text(set_get_thread_count());

		// nas相关参数
        $('#enum_threads-c').text($('#enum_threads').val()+'个');
        $('#sync_threads-c').text($('#sync_threads').val()+'个');
        $('#cores-c').text($('#cores').val()+'核');
        $('#memory_mbytes-c').text($('#memory_mbytes').val()+'MB');
        var net_limit = $('#net_limit').val();
        $('#net_limit-c').text(net_limit==='-1'?'无限制':net_limit+'Mbit/s');
        $('#nas_max_space_val-c').text($('#nas_max_space_val').val()+$('#nas_max_space_unit').val());
	}
}

$('#prev').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');

	$( "#tabs" ).tabs( "option", "active", selindex-1 );
	setPreControlStatus(selindex);


});


function my_next(selindex) {
    $("#tabs").tabs("option", "active", selindex + 1);
    setControlStatus(selindex);
    if (selindex == 0) {
        $(".prev").show();
    }
}

$('#next').click(function() {
	var selindex=$("#tabs").tabs('option', 'active');
	if(selindex==0)
	{
		myAjaxGet('../backup_handle/','a=getstoragedevice',Getstoragedevice);
		var nas_protocol = $("input[name='nas_protocol']:checked").val().toUpperCase();
		var planName = nas_protocol+'('+$('#nas_path').val()+')' + CurentTime();
		if(!isInEdit()){
			$('#taskname').val(planName);
		}
	}
	else if(selindex==1)
	{
		$('#stime0').val() || $('#stime0').val(curTime);
		$('#stime1').val() || $('#stime1').val(curTime);
		$('#stime2').val() || $('#stime2').val(curTime);
		$('#stime3').val() || $('#stime3').val(curTime);
		$('#stime4').val() || $('#stime4').val(curTime);
	}
	else if(selindex==2)
	{
		if(!isInEdit()){
			if($('#uitasktype').val() != 'uitaskpolicy'){
				set_data_keeps_deadline('1');
				set_data_keeps_deadline_unit('month');
				set_get_thread_count('4');
			}
		}
        set_retentionperiod_msg(false);
	}

	if(selindex==4)
	{
		OnFinish();
	}
	else
	{
		if(CheckData(selindex))
		{
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
	RefreshAciTree("Servers_Tree1",'../backup_handle/?a=getlist&id=');
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


function is_norm_plan() {
	var checked_radio = $('input[type=radio][name=bakschedule]:checked');
	return checked_radio.val() !== '1' && checked_radio.val() !== '2';
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

$("input[name='nas_protocol']").click(function(){
	if($(this).val()=='nfs')
	{
		$('.nas_username').hide();
		$('.nas_password').hide();
		$('#nas_path').attr('placeholder','例如：192.168.0.1:/folder 或 192.168.0.1:/');
	}
	else if($(this).val()=='cifs')
	{
		$('.nas_username').show();
		$('.nas_password').show();
		$('#nas_path').attr('placeholder','例如：\\\\192.168.0.1\\folder 或 \\\\192.168.0.1\\');
	}
});
